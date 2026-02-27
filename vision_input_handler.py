import asyncio
import base64
import logging
from typing import Optional, Dict, Any, List
from playwright.async_api import Page

logger = logging.getLogger(__name__)


class VisionInputHandler:
    
    def __init__(self, page: Page, llm_client=None):
        self.page = page
        self.llm_client = llm_client
        self.logger = logging.getLogger(__name__)
    
    async def fill_input_with_vision(self, target_description: str, text_to_input: str) -> Dict[str, Any]:
        try:
            screenshot_b64 = await self.page.screenshot(type='png')
            screenshot_data = base64.b64encode(screenshot_b64).decode('utf-8')
            
            viewport = self.page.viewport_size
            
            input_locations = await self.page.evaluate("""
                () => {
                    const inputs = Array.from(document.querySelectorAll('input[type="text"], input:not([type]), textarea'));
                    return inputs.map((el, idx) => {
                        const rect = el.getBoundingClientRect();
                        const styles = window.getComputedStyle(el);
                        return {
                            index: idx,
                            x: rect.x + rect.width / 2,
                            y: rect.y + rect.height / 2,
                            width: rect.width,
                            height: rect.height,
                            visible: styles.display !== 'none' && styles.visibility !== 'hidden' && rect.width > 0,
                            placeholder: el.placeholder || '',
                            ariaLabel: el.getAttribute('aria-label') || '',
                            name: el.name || '',
                            id: el.id || ''
                        };
                    }).filter(el => el.visible);
                }
            """)
            
            if not input_locations or len(input_locations) == 0:
                return {'success': False, 'error': 'No input fields found on page'}
            
            best_match = None
            best_score = 0
            
            target_lower = target_description.lower()
            for loc in input_locations:
                score = 0
                combined_text = f"{loc.get('placeholder', '')} {loc.get('ariaLabel', '')} {loc.get('name', '')} {loc.get('id', '')}".lower()
                
                if 'from' in target_lower or 'origin' in target_lower:
                    if 'from' in combined_text or 'origin' in combined_text:
                        score += 10
                elif 'to' in target_lower or 'destination' in target_lower:
                    if 'to' in combined_text or 'destination' in combined_text or 'where to' in combined_text:
                        score += 10
                
                if score > best_score:
                    best_score = score
                    best_match = loc
            
            if not best_match:
                best_match = input_locations[0]
            
            self.logger.info(f"Selected input at position ({best_match['x']}, {best_match['y']}) with score {best_score}")
            
            success = await self.page.evaluate("""
                (args) => {
                    const inputs = Array.from(document.querySelectorAll('input[type="text"], input:not([type]), textarea'));
                    const el = inputs[args.index];
                    
                    if (!el) return false;
                    
                    try {
                        el.scrollIntoView({behavior: 'instant', block: 'center'});
                        
                        setTimeout(() => {
                            el.focus();
                            el.click();
                        }, 100);
                        
                        setTimeout(() => {
                            el.value = '';
                            
                            for (let i = 0; i < args.text.length; i++) {
                                el.value += args.text[i];
                                el.dispatchEvent(new InputEvent('input', {
                                    bubbles: true,
                                    cancelable: true,
                                    data: args.text[i],
                                    inputType: 'insertText'
                                }));
                            }
                            
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                        }, 200);
                        
                        return true;
                    } catch (e) {
                        console.error('Input error:', e);
                        return false;
                    }
                }
            """, {'index': best_match['index'], 'text': text_to_input})
            
            await asyncio.sleep(2.5)
            
            actual_value = await self.page.evaluate("""
                (index) => {
                    const inputs = Array.from(document.querySelectorAll('input[type="text"], input:not([type]), textarea'));
                    return inputs[index] ? inputs[index].value : '';
                }
            """, best_match['index'])
            
            self.logger.info(f"Input value after typing: '{actual_value}'")
            
            if text_to_input.lower() in actual_value.lower():
                try:
                    await asyncio.sleep(1)
                    
                    suggestions = await self.page.query_selector_all('li[role="option"], [role="option"]')
                    if suggestions and len(suggestions) > 0:
                        for sugg in suggestions:
                            try:
                                if await sugg.is_visible():
                                    self.logger.info(f"Clicking suggestion")
                                    await sugg.click(force=True, timeout=2000)
                                    await asyncio.sleep(1.5)
                                    break
                            except:
                                continue
                    else:
                        await self.page.keyboard.press('Enter')
                        await asyncio.sleep(1)
                except Exception as e:
                    self.logger.warning(f"Suggestion handling error: {e}")
                    await self.page.keyboard.press('Enter')
                    await asyncio.sleep(1)
                
                return {'success': True, 'value': actual_value}
            else:
                return {'success': False, 'error': f"Expected '{text_to_input}' but got '{actual_value}'"}
                
        except Exception as e:
            self.logger.error(f"Vision input handler error: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return {'success': False, 'error': str(e)}
