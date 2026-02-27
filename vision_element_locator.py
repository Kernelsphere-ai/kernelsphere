import asyncio
import base64
import logging
import json
from typing import Optional, Dict, Any, List, Tuple
from playwright.async_api import Page

logger = logging.getLogger(__name__)


class VisionElementLocator:
    
    def __init__(self, page: Page, llm_client):
        self.page = page
        self.llm_client = llm_client
        self.logger = logging.getLogger(__name__)
    
    async def locate_element_by_description(self, description: str, action_type: str = "click") -> Optional[Dict[str, Any]]:
        try:
            screenshot_bytes = await self.page.screenshot(type='png', full_page=False)
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
            
            viewport = self.page.viewport_size
            
            all_elements = await self.page.evaluate("""
                () => {
                    const elements = [];
                    const all = document.querySelectorAll('*');
                    
                    all.forEach((el, idx) => {
                        const rect = el.getBoundingClientRect();
                        const styles = window.getComputedStyle(el);
                        
                        if (rect.width > 0 && rect.height > 0 && 
                            styles.display !== 'none' && 
                            styles.visibility !== 'hidden' &&
                            rect.top >= 0 && rect.top < window.innerHeight) {
                            
                            const isInteractive = 
                                el.tagName === 'BUTTON' ||
                                el.tagName === 'A' ||
                                el.tagName === 'INPUT' ||
                                el.tagName === 'TEXTAREA' ||
                                el.tagName === 'SELECT' ||
                                el.hasAttribute('onclick') ||
                                el.hasAttribute('role') ||
                                styles.cursor === 'pointer';
                            
                            if (isInteractive) {
                                elements.push({
                                    tag: el.tagName.toLowerCase(),
                                    text: el.innerText?.substring(0, 100) || '',
                                    placeholder: el.placeholder || '',
                                    ariaLabel: el.getAttribute('aria-label') || '',
                                    type: el.type || '',
                                    role: el.getAttribute('role') || '',
                                    x: Math.round(rect.x + rect.width / 2),
                                    y: Math.round(rect.y + rect.height / 2),
                                    width: Math.round(rect.width),
                                    height: Math.round(rect.height),
                                    index: idx
                                });
                            }
                        }
                    });
                    
                    return elements;
                }
            """)
            
            prompt = self._build_vision_prompt(description, action_type, all_elements, viewport)
            
            response = await self.llm_client.generate_with_vision(
                prompt=prompt,
                image_b64=screenshot_b64
            )
            
            result = self._parse_vision_response(response, all_elements)
            
            if result:
                self.logger.info(f"Vision located element at ({result['x']}, {result['y']})")
                return result
            else:
                self.logger.warning("Vision could not locate element")
                return None
                
        except Exception as e:
            self.logger.error(f"Vision element location error: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return None
    
    async def find_input_field_by_vision(self, field_description: str) -> Optional[Dict[str, Any]]:
        try:
            screenshot_bytes = await self.page.screenshot(type='png', full_page=False)
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
            
            input_elements = await self.page.evaluate("""
                () => {
                    const inputs = Array.from(document.querySelectorAll('input, textarea'));
                    return inputs.map((el, idx) => {
                        const rect = el.getBoundingClientRect();
                        const styles = window.getComputedStyle(el);
                        
                        if (rect.width > 0 && rect.height > 0 && 
                            styles.display !== 'none' && 
                            styles.visibility !== 'hidden') {
                            return {
                                index: idx,
                                tag: el.tagName.toLowerCase(),
                                type: el.type || 'text',
                                placeholder: el.placeholder || '',
                                ariaLabel: el.getAttribute('aria-label') || '',
                                name: el.name || '',
                                id: el.id || '',
                                value: el.value || '',
                                x: Math.round(rect.x + rect.width / 2),
                                y: Math.round(rect.y + rect.height / 2),
                                width: Math.round(rect.width),
                                height: Math.round(rect.height)
                            };
                        }
                        return null;
                    }).filter(el => el !== null);
                }
            """)
            
            if not input_elements:
                return None
            
            prompt = f"""You are analyzing a screenshot to locate a specific input field.

Field to find: {field_description}

Available input fields on the page:
{json.dumps(input_elements, indent=2)}

Based on the screenshot and the field descriptions, identify which input field matches "{field_description}".

Consider:
1. Visual position and context in the screenshot
2. Placeholder text
3. Aria labels
4. Position relative to other elements
5. Visual labels near the input field

Respond with ONLY a JSON object:
{{
    "index": <index of matching input>,
    "confidence": <0.0 to 1.0>,
    "reasoning": "why this input matches"
}}

If no good match, respond with {{"index": -1, "confidence": 0.0, "reasoning": "explanation"}}"""
            
            response = await self.llm_client.generate_with_vision(
                prompt=prompt,
                image_b64=screenshot_b64
            )
            
            try:
                result = json.loads(response.strip())
                
                if result.get('index', -1) >= 0 and result.get('confidence', 0) > 0.5:
                    matched_input = input_elements[result['index']]
                    self.logger.info(f"Vision matched input: {matched_input} (confidence: {result['confidence']})")
                    return matched_input
                else:
                    self.logger.warning(f"Vision could not confidently match input: {result.get('reasoning')}")
                    return None
                    
            except json.JSONDecodeError:
                self.logger.error("Failed to parse vision response as JSON")
                return None
                
        except Exception as e:
            self.logger.error(f"Vision input detection error: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return None
    
    async def get_page_structure(self) -> Dict[str, Any]:
        try:
            screenshot_bytes = await self.page.screenshot(type='png', full_page=False)
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
            
            prompt = """Analyze this screenshot and describe the page structure.

Identify:
1. Main sections (header, navigation, content, footer)
2. Interactive elements (buttons, inputs, links)
3. Current state (loaded, loading, error, etc)
4. Any popups, modals, or overlays
5. Input fields and their purposes

Respond with a JSON object:
{
    "page_type": "search|form|results|article|error|other",
    "main_sections": ["section1", "section2"],
    "interactive_elements": [
        {"type": "button|input|link", "purpose": "description", "location": "description"}
    ],
    "popups_detected": true/false,
    "form_fields": [
        {"field_type": "text|date|select", "label": "description"}
    ],
    "page_state": "ready|loading|error",
    "recommendations": "what should be done next"
}"""
            
            response = await self.llm_client.generate_with_vision(
                prompt=prompt,
                image_b64=screenshot_b64
            )
            
            try:
                structure = json.loads(response.strip())
                return structure
            except json.JSONDecodeError:
                return {"error": "Failed to parse structure", "raw_response": response}
                
        except Exception as e:
            self.logger.error(f"Page structure analysis error: {e}")
            return {"error": str(e)}
    
    async def verify_action_result(self, expected_change: str) -> Dict[str, Any]:
        try:
            screenshot_bytes = await self.page.screenshot(type='png', full_page=False)
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
            
            prompt = f"""Analyze this screenshot to verify if an expected change occurred.

Expected change: {expected_change}

Look at the page and determine:
1. Did the expected change occur?
2. What actually happened?
3. Are there any error messages or warnings?
4. Is the page in a loading state?

Respond with JSON:
{{
    "change_occurred": true/false,
    "actual_result": "description of what you see",
    "errors_detected": true/false,
    "error_message": "any error text visible",
    "is_loading": true/false,
    "confidence": <0.0 to 1.0>
}}"""
            
            response = await self.llm_client.generate_with_vision(
                prompt=prompt,
                image_b64=screenshot_b64
            )
            
            try:
                result = json.loads(response.strip())
                return result
            except json.JSONDecodeError:
                return {"error": "Failed to parse verification", "raw_response": response}
                
        except Exception as e:
            self.logger.error(f"Action verification error: {e}")
            return {"error": str(e)}
    
    def _build_vision_prompt(self, description: str, action_type: str, elements: List[Dict], viewport: Dict) -> str:
        prompt = f"""You are analyzing a screenshot to locate a specific element for a web automation task.

Task: {action_type} on element matching "{description}"

Screenshot dimensions: {viewport['width']}x{viewport['height']}

Interactive elements found on page (with coordinates):
{json.dumps(elements[:50], indent=2)}

Instructions:
1. Look at the screenshot carefully
2. Find the element that best matches "{description}"
3. Consider visual context, position, text, and element type
4. Verify the element is clickable/interactable

Respond with ONLY a JSON object:
{{
    "element_index": <index from the elements list above, or -1 if not found>,
    "coordinates": {{"x": <pixel x>, "y": <pixel y>}},
    "confidence": <0.0 to 1.0>,
    "reasoning": "why this element matches"
}}

If you cannot find a matching element, return {{"element_index": -1, "confidence": 0.0, "reasoning": "explanation"}}"""
        
        return prompt
    
    def _parse_vision_response(self, response: str, all_elements: List[Dict]) -> Optional[Dict[str, Any]]:
        try:
            response_clean = response.strip()
            if response_clean.startswith('```json'):
                response_clean = response_clean[7:]
            if response_clean.startswith('```'):
                response_clean = response_clean[3:]
            if response_clean.endswith('```'):
                response_clean = response_clean[:-3]
            response_clean = response_clean.strip()
            
            result = json.loads(response_clean)
            
            if result.get('element_index', -1) >= 0 and result.get('confidence', 0) > 0.6:
                element_idx = result['element_index']
                if element_idx < len(all_elements):
                    element = all_elements[element_idx]
                    return {
                        'x': result['coordinates']['x'],
                        'y': result['coordinates']['y'],
                        'element': element,
                        'confidence': result['confidence'],
                        'reasoning': result.get('reasoning', '')
                    }
            
            return None
            
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            self.logger.error(f"Failed to parse vision response: {e}")
            self.logger.error(f"Response was: {response}")
            return None