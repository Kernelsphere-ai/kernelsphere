import logging
from typing import List, Dict, Any
from playwright.async_api import Page

from models import DOMElement, DOMState

logger = logging.getLogger(__name__)


DOM_EXTRACTION_SCRIPT = """
() => {
    const elements = [];
    let index = 0;
    
    const selectors = [
        'a[href]',
        'button',
        'input',
        'textarea',
        'select',
        '[role="button"]',
        '[role="link"]',
        '[role="textbox"]',
        '[role="searchbox"]',
        '[onclick]',
        '[role="tab"]',
        '[role="menuitem"]',
        '[contenteditable="true"]'
    ];
    
    function isVisible(el) {
        if (!el) return false;
        
        const style = window.getComputedStyle(el);
        if (style.display === 'none') return false;
        if (style.visibility === 'hidden') return false;
        if (style.opacity === '0') return false;
        
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return false;
        
        return true;
    }
    
    function getVisibleText(el) {
        let text = '';
        
        if (el.innerText) {
            text = el.innerText.trim();
        }
        else if (el.textContent) {
            text = el.textContent.trim();
        }
        
        if (text.length > 100) {
            text = text.substring(0, 97) + '...';
        }
        
        return text;
    }
    
    function getAttributes(el) {
        const attrs = {};
        
        const importantAttrs = [
            'id','class','name','type','placeholder','value','href','title',
            'aria-label','aria-checked','aria-selected','aria-current',
            'role','min','max','step','data-date','datetime'
        ];

        const role = el.getAttribute('role');
        const inputType = el.type || null;
        const checked = el.checked ?? null;
        const disabled = el.disabled ?? null;

        
        for (const attr of importantAttrs) {
            const value = el.getAttribute(attr);
            if (value) {
                attrs[attr] = value;
            }
        }
        
        return attrs;
    }
    
    function getXPath(el) {
        if (el.id) {
            return `//*[@id="${el.id}"]`;
        }
        
        const parts = [];
        while (el && el.nodeType === Node.ELEMENT_NODE) {
            let idx = 1;
            let sibling = el.previousSibling;
            while (sibling) {
                if (sibling.nodeType === Node.ELEMENT_NODE && sibling.tagName === el.tagName) {
                    idx++;
                }
                sibling = sibling.previousSibling;
            }
            
            const tagName = el.tagName.toLowerCase();
            const part = idx > 1 ? `${tagName}[${idx}]` : tagName;
            parts.unshift(part);
            
            el = el.parentNode;
        }
        
        return parts.length ? '/' + parts.join('/') : '';
    }
    
    function getImportanceScore(el, tag, text, attrs) {
        let score = 0;
        
        if (tag === 'button') score += 10;
        else if (tag === 'a') score += 8;
        else if (tag === 'input') score += 9;
        else if (tag === 'select') score += 7;
        
        if (text.length > 0) score += 5;
        if (text.toLowerCase().includes('submit')) score += 10;
        if (text.toLowerCase().includes('search')) score += 8;
        if (text.toLowerCase().includes('login')) score += 7;
        if (text.toLowerCase().includes('sign in')) score += 7;
        if (text.toLowerCase().includes('buy')) score += 6;
        if (text.toLowerCase().includes('add')) score += 5;
        
        if (attrs.type === 'submit') score += 10;
        if (attrs.type === 'search') score += 8;
        if (attrs.role === 'button') score += 5;
        if (attrs['aria-label']) score += 3;
        
        const rect = el.getBoundingClientRect();
        if (rect.top >= 0 && rect.top <= window.innerHeight) {
            score += 5;
        }
        
        return score;
    }
    
    for (const selector of selectors) {
        try {
            const foundElements = document.querySelectorAll(selector);
            
            for (const el of foundElements) {
                if (!isVisible(el)) continue;
                
                const tag = el.tagName.toLowerCase();
                const text = getVisibleText(el);
                const attrs = getAttributes(el);
                const xpath = getXPath(el);
                const importance = getImportanceScore(el, tag, text, attrs);
                
                elements.push({
                    index: index,
                    tag: tag,
                    text: text,
                    attributes: attrs,
                    xpath: xpath,
                    importance: importance
                });
                
                el.setAttribute('data-automation-index', index);
                
                index++;
                
                if (index >= 250) break;
            }
            
            if (index >= 250) break;
            
        } catch (e) {
            console.error('Error with selector:', selector, e);
        }
    }
    
    elements.sort((a, b) => b.importance - a.importance);
    
    const topElements = elements.slice(0, 200);
    
    return topElements;
}
"""


class DOMService:
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.captcha_cleared = False
        self.cloudflare_cleared = False
        self.cached_elements = {}
    
    def mark_captcha_cleared(self):
        self.captcha_cleared = True
        self.logger.info("CAPTCHA marked as cleared")
    
    def mark_cloudflare_cleared(self):
        self.cloudflare_cleared = True
        self.logger.info("Cloudflare marked as cleared")
    
    async def get_simplified_dom(self, page: Page) -> DOMState:
        return await self.extract_dom_state(page)
    
    async def extract_dom_state(self, page: Page) -> DOMState:
        try:
            url = page.url
            title = await page.title()
            
            elements_data = await page.evaluate(DOM_EXTRACTION_SCRIPT)
            
            elements = []
            for elem_data in elements_data:
                try:
                    element = DOMElement(
                        index=elem_data['index'],
                        tag=elem_data['tag'],
                        text=elem_data.get('text', ''),
                        attributes=elem_data.get('attributes', {}),
                        xpath=elem_data.get('xpath', '')
                    )
                    elements.append(element)
                except Exception as e:
                    self.logger.warning(f"Failed to parse element: {e}")
                    continue
            
            self.cached_elements = {elem.index: elem for elem in elements}
            
            text_content = await self._extract_text_content(page)
            
            has_cookie_popup = await self._detect_cookie_popup(page)
            has_cloudflare = False if self.cloudflare_cleared else await self._detect_cloudflare(page)
            has_captcha = False if self.captcha_cleared else await self._detect_captcha(page)
            
            dom_state = DOMState(
                url=url,
                title=title,
                elements=elements,
                text_content=text_content,
                has_cookie_popup=has_cookie_popup,
                has_cloudflare=has_cloudflare,
                has_captcha=has_captcha
            )
            
            self.logger.info(f"Extracted {len(elements)} interactive elements from {url}")
            
            return dom_state
            
        except Exception as e:
            self.logger.error(f"Error extracting DOM: {e}")
            return DOMState(
                url=page.url,
                title="Error",
                elements=[],
                text_content="",
                has_cookie_popup=False,
                has_cloudflare=False,
                has_captcha=False
            )
    
    async def get_clickable_elements(self):
        clickable_tags = ['a', 'button']
        clickable_roles = ['button', 'link', 'tab', 'menuitem']
        
        elements = []
        for elem in self.cached_elements.values():
            if elem.tag in clickable_tags:
                elements.append(elem)
            elif elem.attributes.get('role') in clickable_roles:
                elements.append(elem)
            elif elem.attributes.get('onclick'):
                elements.append(elem)
        
        return elements
    
    async def get_input_elements(self):
        input_tags = ['input', 'textarea']
        input_roles = ['textbox', 'searchbox']
        
        elements = []
        for elem in self.cached_elements.values():
            if elem.tag in input_tags:
                elements.append(elem)
            elif elem.attributes.get('role') in input_roles:
                elements.append(elem)
            elif elem.attributes.get('contenteditable') == 'true':
                elements.append(elem)
        
        return elements
    
    async def get_dropdown_elements(self):
        elements = []
        for elem in self.cached_elements.values():
            if elem.tag == 'select':
                elements.append(elem)
        
        return elements
    
    async def get_element_by_index(self, page: Page, index: int):
        try:
            selector = f'[data-automation-index="{index}"]'
            element = await page.query_selector(selector)
            return element
        except Exception as e:
            self.logger.error(f"Error getting element {index}: {e}")
            return None
    
    async def _extract_text_content(self, page: Page, max_length: int = 5000) -> str:
        try:
            text = await page.text_content('body') or ""
            
            text = ' '.join(text.split())
            
            if len(text) > max_length:
                text = text[:max_length] + "..."
            
            return text
        except:
            return ""
    
    async def _detect_cookie_popup(self, page: Page) -> bool:
        try:
            selectors = [
                'button:has-text("Accept")',
                '#onetrust-accept-btn-handler',
                '[class*="cookie"]',
            ]
            
            for selector in selectors:
                element = await page.query_selector(selector)
                if element:
                    try:
                        if await element.is_visible():
                            return True
                    except:
                        pass
            
            return False
        except:
            return False
    
    async def _detect_cloudflare(self, page: Page) -> bool:
        try:
            text = await page.text_content('body') or ""
            text_lower = text.lower()
            
            cloudflare_phrases = [
                "checking your browser before accessing",
                "just a moment..."
            ]
            
            has_cloudflare_text = any(phrase in text_lower for phrase in cloudflare_phrases)
            
            if not has_cloudflare_text:
                return False
            
            cf_elements = await page.query_selector_all(
                "iframe[src*='challenges.cloudflare.com'], [class*='cf-browser-verification']"
            )
            
            return len(cf_elements) > 0
            
        except:
            return False
    
    async def _detect_captcha(self, page: Page) -> bool:
        try:
            recaptcha_iframe = await page.query_selector('iframe[src*="recaptcha"]')
            if recaptcha_iframe and await recaptcha_iframe.is_visible():
                return True
            
            hcaptcha_iframe = await page.query_selector('iframe[src*="hcaptcha"]')
            if hcaptcha_iframe and await hcaptcha_iframe.is_visible():
                return True
            
            recaptcha_div = await page.query_selector('.g-recaptcha')
            if recaptcha_div and await recaptcha_div.is_visible():
                return True
            
            hcaptcha_div = await page.query_selector('.h-captcha')
            if hcaptcha_div and await hcaptcha_div.is_visible():
                return True
            
            return False
        except:
            return False
    
    def format_elements_for_prompt(self, elements: List[DOMElement]) -> str:
        if not elements:
            return "No interactive elements found."
        
        lines = []
        for elem in elements[:100]:
            attrs_str = ""
            if elem.attributes:
                important = ['type', 'name', 'placeholder', 'value', 'aria-label', 'href']
                attr_parts = []
                for key in important:
                    if key in elem.attributes:
                        value = elem.attributes[key]
                        if len(value) > 50:
                            value = value[:47] + "..."
                        attr_parts.append(f'{key}="{value}"')
                
                if attr_parts:
                    attrs_str = " {" + ", ".join(attr_parts) + "}"
            
            text = elem.text if elem.text else ""
            if len(text) > 60:
                text = text[:57] + "..."
            
            line = f"[{elem.index}]<{elem.tag}>{text}</{elem.tag}>{attrs_str}"
            lines.append(line)
        
        return "\n".join(lines)
    
    def format_elements_for_llm(self, elements: List[DOMElement]) -> str:
        return self.format_elements_for_prompt(elements)