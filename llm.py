import os
import json
import logging
import re
from typing import Dict, Any, Optional, Union
import google.generativeai as genai
from pydantic import ValidationError
import asyncio

from models import AgentAction, AgentDecision, ExtractionResult
from models import (
    NavigateAction, ClickElementAction, InputTextAction, SelectDropdownAction,
    SearchAction, ScrollAction, GoBackAction, ExtractAction, SendKeysAction,
    WaitAction, CloseCookiePopupAction, ClosePopupAction, DoneAction,
    SetPriceRangeAction, SelectDateAction, ExtractAllrecipesRecipeAction
)


logger = logging.getLogger(__name__)


class GeminiLLM:
    """Gemini LLM with strict action schema enforcement"""
    
    def __init__(
        self,
        model_name: str = "gemini-2.0-flash",
        api_key: Optional[str] = None
    ):
        """
        Initialize Gemini LLM
        
        Args:
            model_name: Gemini model to use
            temperature: Generation temperature (0.0-0.2 for control, 0.5-0.7 for extraction)
            api_key: Google API key (from env if not provided)
        """
        self.model_name = model_name
        self.temperature = temperature
        
        # Configure API
        api_key = api_key or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY not found in environment")
        
        genai.configure(api_key=api_key)
        
        # CONTROL MODEL: Low temperature for strict action selection
        self.control_model = genai.GenerativeModel(
            model_name=model_name,
            generation_config={
                "temperature": 0.1,  # Very low for deterministic control
                "response_mime_type": "application/json",
            }
        )
        
        # EXTRACTION MODEL: Higher temperature for creative extraction
        self.extraction_model = genai.GenerativeModel(
            model_name=model_name,
            generation_config={
                "temperature": 0.5,  # Higher for content generation
                "response_mime_type": "application/json",
            }
        )
        
        logger.info(f"Initialized Gemini models: {model_name}")
        logger.info(f"Control temp: 0.1, Extraction temp: 0.5")
        
        self.request_count = 0
        self.last_request_time = 0
    
    
    async def _rate_limit(self):
        import time
        current_time = time.time()
        
        if self.request_count > 0:
            time_since_last = current_time - self.last_request_time
            if time_since_last < 1.0:
                await asyncio.sleep(1.0 - time_since_last)
        
        self.last_request_time = time.time()
        self.request_count += 1
    
    def decide_action(
        self,
        system_prompt: str,
        user_message: str,
        conversation_history: Optional[list] = None,
        max_retries: int = 3,
        screenshot: Optional[bytes] = None
    ) -> Union[AgentAction, AgentDecision]:
        """
        Get action decision from LLM with STRICT validation
        
        CRITICAL CHANGE: Returns AgentAction (strict union), not AgentDecision
        Retries with repair instead of silent fallback to extract
        
        Args:
            system_prompt: System instructions
            user_message: Current observation/state
            conversation_history: Previous messages
            max_retries: Number of repair attempts before failing
            
        Returns:
            AgentAction (strict union type) or raises exception
        """
        last_error = None
        last_response = None
        
        for attempt in range(max_retries):
            try:
                # Build prompt (with repair instruction if retry)
                if attempt == 0:
                    prompt = self._build_decision_prompt(
                        system_prompt,
                        user_message,
                        conversation_history
                    )
                else:
                    # REPAIR PROMPT: Tell model what went wrong
                    prompt = self._build_repair_prompt(
                        system_prompt,
                        user_message,
                        last_response,
                        last_error,
                        attempt
                    )
                
                # Generate response with CONTROL model (low temp)
                response = self.control_model.generate_content(prompt)
                response_text = response.text.strip()
                
                logger.debug(f"LLM attempt {attempt + 1}: {response_text[:200]}...")
                
                # Clean response
                response_text = self._clean_json_response(response_text)
                
                # Parse JSON
                try:
                    response_json = json.loads(response_text)
                except json.JSONDecodeError as e:
                    # Try to repair JSON
                    response_text = self._repair_json(response_text)
                    response_json = json.loads(response_text)
                
                # Parse into STRICT AgentAction union
                action = self._parse_strict_action(response_json)
                
                logger.info(f"Valid action: {action.action}")
                return action
                
            except json.JSONDecodeError as e:
                last_error = f"JSON parse error: {e}"
                last_response = response_text
                logger.warning(f"Attempt {attempt + 1} failed: {last_error}")
                
            except ValidationError as e:
                last_error = f"Schema validation error: {e}"
                last_response = response_text
                logger.warning(f"Attempt {attempt + 1} failed: {last_error}")
                
            except ValueError as e:
                last_error = f"Action validation error: {e}"
                last_response = response_text
                logger.warning(f"Attempt {attempt + 1} failed: {last_error}")
            
            except Exception as e:
                last_error = f"Unexpected error: {e}"
                last_response = response_text if response_text else str(e)
                logger.warning(f"Attempt {attempt + 1} failed: {last_error}")
     
        logger.error(f" All {max_retries} attempts failed. Last error: {last_error}")
        logger.error(f"Last response: {last_response}")
        
        return DoneAction(
            reasoning=f"LLM failed to produce valid action after {max_retries} attempts: {last_error}",
            action="done",
            success=False,
            extracted_content=f"Agent control failure: {last_error}"
        )
    
    def _parse_strict_action(self, response_json: Dict[str, Any]) -> AgentAction:
        """
        Parse JSON into STRICT AgentAction union
        
        Args:
            response_json: Raw JSON from LLM
            
        Returns:
            Validated action from AgentAction union
            
        Raises:
            ValueError: If action invalid or missing required fields
        """
        action_type = response_json.get("action")
        
        if not action_type:
            raise ValueError("Missing 'action' field")
        
        # Map action strings to strict models
        ACTION_MAP = {
            "navigate": NavigateAction,
            "click_element": ClickElementAction,
            "input_text": InputTextAction,
            "select_dropdown": SelectDropdownAction,
            "search": SearchAction,
            "scroll": ScrollAction,
            "go_back": GoBackAction,
            "extract": ExtractAction,
            "send_keys": SendKeysAction,
            "wait": WaitAction,
            "set_price_range": SetPriceRangeAction,
            "select_date": SelectDateAction,
            "extract_allrecipes_recipe": ExtractAllrecipesRecipeAction,
            "close_cookie_popup": CloseCookiePopupAction,
            "close_popup": ClosePopupAction,
            "done": DoneAction
        }
        
        if action_type not in ACTION_MAP:
            valid_actions = list(ACTION_MAP.keys())
            raise ValueError(
                f"Invalid action '{action_type}'. Must be one of: {valid_actions}"
            )
        
        # Parse with strict model
        action_class = ACTION_MAP[action_type]
        
        try:
            validated_action = action_class(**response_json)
            return validated_action
        except ValidationError as e:
            # Extract specific field errors
            errors = []
            for error in e.errors():
                field = error['loc'][0] if error['loc'] else 'unknown'
                msg = error['msg']
                errors.append(f"{field}: {msg}")
            
            raise ValueError(
                f"Action '{action_type}' validation failed: {'; '.join(errors)}"
            )
    
    def extract_content(
        self,
        system_prompt: str,
        extraction_goal: str,
        page_content: str
    ) -> ExtractionResult:
        
        try:
            prompt = f"""{system_prompt}

EXTRACTION GOAL: {extraction_goal}

PAGE CONTENT:
{page_content[:15000]}

Extract the requested information and return ONLY a JSON object:
{{
    "extracted_content": "the extracted information",
    "confidence": 0.95,
    "source": "page_content"
}}

JSON response:"""
            
            response = self.extraction_model.generate_content(prompt)
            response_text = response.text.strip()
            
            response_text = self._clean_json_response(response_text)
            
            parsed = None
            try:
                parsed = json.loads(response_text)
            except json.JSONDecodeError as e:
                logger.warning(f"Initial JSON parse failed: {e}, attempting repair")
                try:
                    repaired = self._repair_json(response_text)
                    parsed = json.loads(repaired)
                    logger.info("JSON repair successful")
                except json.JSONDecodeError as e2:
                    logger.warning(f"JSON repair failed: {e2}, extracting first valid object")
                    
                    brace_count = 0
                    start_idx = response_text.find('{')
                    if start_idx != -1:
                        for i in range(start_idx, len(response_text)):
                            if response_text[i] == '{':
                                brace_count += 1
                            elif response_text[i] == '}':
                                brace_count -= 1
                                if brace_count == 0:
                                    try:
                                        parsed = json.loads(response_text[start_idx:i+1])
                                        logger.info("Extracted first valid JSON object")
                                        break
                                    except:
                                        continue
            
            if parsed is None:
                logger.error("All JSON parsing attempts failed, using raw response")
                return ExtractionResult(
                    extracted_content=response_text[:500],
                    confidence=0.2,
                    source="raw_fallback"
                )
            
            if isinstance(parsed, dict):
                if "extracted_content" not in parsed:
                    alt = parsed.get("answer") or parsed.get("result") or parsed.get("content")
                    parsed = {
                        "extracted_content": alt if alt is not None else parsed,
                        "confidence": parsed.get("confidence", 0.5),
                        "source": parsed.get("source", "page_content"),
                    }
                else:
                    parsed.setdefault("confidence", 0.5)
                    parsed.setdefault("source", "page_content")
            else:
                parsed = {
                    "extracted_content": parsed,
                    "confidence": 0.5,
                    "source": "page_content",
                }

            result = ExtractionResult(**parsed)
            return result

            
        except Exception as e:
            logger.error(f"Extraction error: {e}")
            return ExtractionResult(
                extracted_content=page_content[:500],
                confidence=0.3,
                source="fallback"
            )
    
    def _build_decision_prompt(
        self,
        system_prompt: str,
        user_message: str,
        conversation_history: Optional[list] = None,
        screenshot: Optional[bytes] = None
    ) -> Union[str, list]:
        """Build complete prompt for decision making"""
        
        # STRICT format instructions with examples
        format_instructions = """
You must respond with ONLY a valid JSON object matching ONE of these action formats:

NAVIGATE:
{"reasoning": "why navigating", "action": "navigate", "url": "https://example.com"}

CLICK ELEMENT:
{"reasoning": "why clicking", "action": "click_element", "index": 42}

INPUT TEXT:
{"reasoning": "why inputting", "action": "input_text", "index": 42, "text": "search query"}

SELECT DROPDOWN:
{"reasoning": "why selecting", "action": "select_dropdown", "index": 42, "option": "option text"}

SEARCH:
{"reasoning": "why searching", "action": "search", "query": "search terms"}

SCROLL:
{"reasoning": "why scrolling", "action": "scroll", "direction": "down", "amount": 500}

GO BACK:
{"reasoning": "why going back", "action": "go_back"}

SET PRICE RANGE:
{"reasoning": "why setting price range", "action": "set_price_range", "min_price": 0, "max_price": 25}

SELECT DATE:
{"reasoning": "why selecting date", "action": "select_date", "date": "2026-01-2"}

EXTRACT ALLRECIPES RECIPE:
{"reasoning": "why extracting recipe data", "action": "extract_allrecipes_recipe"}

EXTRACT:
{"reasoning": "why extracting", "action": "extract", "extraction_goal": "what to extract"}

SEND KEYS:
{"reasoning": "why sending keys", "action": "send_keys", "keys": "Enter"}

WAIT:
{"reasoning": "why waiting", "action": "wait", "duration": 2.0}

CLOSE COOKIE POPUP:
{"reasoning": "why closing", "action": "close_cookie_popup"}

CLOSE POPUP:
{"reasoning": "why closing", "action": "close_popup"}

DONE:
{"reasoning": "why done", "action": "done", "success": true, "extracted_content": "final answer"}

CRITICAL RULES:
1. Action MUST be exactly one of the above (no variations like "click" or "scroll_down")
2. Include ALL required fields for your chosen action
3. Do NOT include fields from other actions
4. Return ONLY the JSON object - no markdown, no explanation, no code blocks

WHEN TO USE "DONE":
- ONLY call "done" when you have COMPLETELY FINISHED the ENTIRE task
- ALL parts of the task must be accomplished (e.g., if task is "login AND create wishlist", both must be done)
- DO NOT call "done" if you have only completed part of the task
- DO NOT call "done" just because you successfully completed one step
- DO NOT call "done" if there are still actions remaining to complete the full task
- If unsure whether task is complete, continue with next logical action instead of calling "done"

INVALID JSON WILL BE REJECTED AND YOU WILL BE ASKED TO TRY AGAIN.
"""
        
        # Build full prompt
        if conversation_history:
            history_text = "\n\n".join([
                f"Step {i+1}: {msg}" 
                for i, msg in enumerate(conversation_history[-5:])
            ])
            full_prompt = f"""{system_prompt}

{format_instructions}

CONVERSATION HISTORY:
{history_text}

CURRENT STATE:
{user_message}

JSON response:"""
        else:
            full_prompt = f"""{system_prompt}

{format_instructions}

CURRENT STATE:
{user_message}

JSON response:"""
        
        if screenshot:
            import PIL.Image
            import io
            image = PIL.Image.open(io.BytesIO(screenshot))
            return [full_prompt, image]
        
        return full_prompt
    
    def _build_repair_prompt(
        self,
        system_prompt: str,
        user_message: str,
        failed_response: str,
        error_message: str,
        attempt: int,
        screenshot: Optional[bytes] = None
    ) -> Union[str, list]:
        """Build repair prompt after validation failure"""
        
        repair_instructions = f"""
YOUR PREVIOUS RESPONSE WAS INVALID AND REJECTED.

Attempt: {attempt + 1}
Error: {error_message}
Your response: {failed_response[:500]}

COMMON MISTAKES TO AVOID:
1. Using wrong action name (e.g., "click" instead of "click_element")
2. Missing required fields (e.g., "index" for click_element)
3. Including extra fields not needed for the action
4. Malformed JSON (missing quotes, trailing commas)

PLEASE PROVIDE A CORRECTED JSON RESPONSE.
Review the action formats carefully and match them exactly.

CURRENT STATE:
{user_message}

CORRECTED JSON response:"""
        
        full_prompt = system_prompt + "\n\n" + repair_instructions
        
        if screenshot:
            import PIL.Image
            import io
            image = PIL.Image.open(io.BytesIO(screenshot))
            return [full_prompt, image]
        
        return full_prompt
    
    def _clean_json_response(self, response: str) -> str:
        """Clean up JSON response"""
        # Remove markdown code blocks
        response = response.replace("```json", "").replace("```", "")
        
        # Strip whitespace
        response = response.strip()
        
        # If response starts with explanation, extract JSON
        if not response.startswith("{"):
            start = response.find("{")
            end = response.rfind("}")
            if start != -1 and end != -1:
                response = response[start:end+1]
        
        return response
    
    def _repair_json(self, text: str) -> str:
        """
        Attempt to repair malformed JSON
        Common issues: trailing commas, unquoted keys, single quotes
        """
        # Remove trailing commas before } or ]
        text = re.sub(r',(\s*[}\]])', r'\1', text)
        
        text = text.replace("'", '"')
        
        # Remove any trailing content after final }
        last_brace = text.rfind('}')
        if last_brace != -1:
            text = text[:last_brace+1]
        
        return text