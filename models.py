from typing import Optional, List, Dict, Any, Literal, Union
from pydantic import BaseModel, Field
import hashlib
import re


class BaseAction(BaseModel):
    reasoning: Optional[str] = ""



class NavigateAction(BaseAction):
    action: Literal["navigate"] = "navigate"
    url: str


class ClickElementAction(BaseAction):
    action: Literal["click_element"] = "click_element"
    index: int


class InputTextAction(BaseAction):
    action: Literal["input_text"] = "input_text"
    index: int
    text: str


class SelectDropdownAction(BaseAction):
    action: Literal["select_dropdown"] = "select_dropdown"
    index: int
    option: str

class SetPriceRangeAction(BaseAction):
    action: Literal["set_price_range"] = "set_price_range"
    min_price: Optional[float] = None
    max_price: Optional[float] = None


class SelectDateAction(BaseAction):
    action: Literal["select_date"] = "select_date"
    date: str


class ExtractAllrecipesRecipeAction(BaseAction):
    action: Literal["extract_allrecipes_recipe"] = "extract_allrecipes_recipe"


class SearchAction(BaseAction):
    action: Literal["search"] = "search"
    query: str


class ScrollAction(BaseAction):
    action: Literal["scroll"] = "scroll"
    direction: Literal["up", "down"] = Field(default="down")
    amount: int = Field(default=500)


class GoBackAction(BaseAction):
    action: Literal["go_back"] = "go_back"


class ExtractAction(BaseAction):
    action: Literal["extract"] = "extract"
    extraction_goal: str


class SendKeysAction(BaseAction):
    action: Literal["send_keys"] = "send_keys"
    keys: str


class WaitAction(BaseAction):
    action: Literal["wait"] = "wait"
    duration: float = Field(default=2.0)


class CloseCookiePopupAction(BaseAction):
    action: Literal["close_cookie_popup"] = "close_cookie_popup"


class ClosePopupAction(BaseAction):
    action: Literal["close_popup"] = "close_popup"


class DoneAction(BaseAction):
    action: Literal["done"] = "done"
    success: bool
    extracted_content: str


AgentAction = Union[
    NavigateAction,
    ClickElementAction,
    InputTextAction,
    SelectDropdownAction,
    SearchAction,
    ScrollAction,
    GoBackAction,
    ExtractAction,
    SendKeysAction,
    SetPriceRangeAction,
    SelectDateAction,
    ExtractAllrecipesRecipeAction,
    WaitAction,
    CloseCookiePopupAction,
    ClosePopupAction,
    DoneAction
]


class AgentDecision(BaseModel):
    reasoning: str
    action: str
    index: Optional[int] = None
    text: Optional[str] = None
    url: Optional[str] = None
    query: Optional[str] = None
    option: Optional[str] = None
    direction: Optional[str] = None
    amount: Optional[int] = None
    keys: Optional[str] = None
    duration: Optional[float] = None
    success: Optional[bool] = None
    extracted_content: Optional[str] = None
    extraction_goal: Optional[str] = None


class DOMElement(BaseModel):
    index: int
    tag: str
    text: str = ""
    attributes: Dict[str, str] = Field(default_factory=dict)
    xpath: str = ""





class DOMState(BaseModel):
    url: str
    title: str
    elements: List[DOMElement]
    text_content: str = ""
    has_cookie_popup: bool = False
    has_cloudflare: bool = False
    has_captcha: bool = False
    
    def get_dom_hash(self) -> str:
        state_str = f"{self.url}|{self.title}|{len(self.elements)}"
        
        for elem in self.elements[:10]:
            state_str += f"|{elem.tag}:{elem.text[:20]}"
        
        if self.text_content:
            text_hash = hashlib.md5(self.text_content[:500].encode()).hexdigest()[:8]
            state_str += f"|text:{text_hash}"
        
        return hashlib.md5(state_str.encode()).hexdigest()


class ActionResult(BaseModel):
    action: str
    success: bool
    error: Optional[str] = None
    extracted_content: Optional[str] = None
    url: Optional[str] = None
    title: Optional[str] = None
    
    url_changed: bool = False
    dom_changed: bool = False
    new_dialog: bool = False
    state_changed: bool = False
    
    def model_post_init(self, __context):
        if not self.state_changed:
            self.state_changed = self.url_changed or self.dom_changed or self.new_dialog


class StepResult(BaseModel):
    step: int
    url: str
    title: str
    actions: List[ActionResult]
    dom_hash: Optional[str] = None



class AgentHistory(BaseModel):
    task: str
    start_url: str
    success: bool
    final: Dict[str, str]
    steps: List[StepResult]
    total_steps: int


class ProgressState(BaseModel):
    current_url: str = ""
    current_dom_hash: str = ""
    last_url: str = ""
    last_dom_hash: str = ""
    action_history: List[tuple] = Field(default_factory=list)
    consecutive_same_actions: int = 0
    consecutive_wait_actions: int = 0
    consecutive_no_progress: int = 0
    stagnation_detected: bool = False
    
    def update(self, url: str, dom_hash: str, action: str, element_index: Optional[int] = None):
        url_changed = url != self.current_url
        dom_changed = dom_hash != self.current_dom_hash
        
        self.last_url = self.current_url
        self.last_dom_hash = self.current_dom_hash
        self.current_url = url
        self.current_dom_hash = dom_hash
        
        action_key = (action, element_index)
        
        if self.action_history and self.action_history[-1] == action_key:
            self.consecutive_same_actions += 1
        else:
            self.consecutive_same_actions = 1
        
        self.action_history.append(action_key)
        if len(self.action_history) > 10:
            self.action_history.pop(0)
        
        if action == "wait":
            self.consecutive_wait_actions += 1
        else:
            self.consecutive_wait_actions = 0
        
        if not url_changed and not dom_changed and action not in ["wait", "scroll", "extract"]:
            self.consecutive_no_progress += 1
        else:
            self.consecutive_no_progress = 0
        
        self.stagnation_detected = (
            self.consecutive_same_actions >= 2 or
            self.consecutive_wait_actions >= 2 or
            self.consecutive_no_progress >= 3
        )
        
        return url_changed or dom_changed
    
    def get_stagnation_reason(self) -> Optional[str]:
        if not self.stagnation_detected:
            return None
        
        if self.consecutive_same_actions >= 2:
            action = self.action_history[-1][0]
            index = self.action_history[-1][1]
            return f"Repeating same action '{action}' on element {index} {self.consecutive_same_actions} times with no effect"
        
        if self.consecutive_wait_actions >= 2:
            return f"Waiting {self.consecutive_wait_actions} consecutive times"
        
        if self.consecutive_no_progress >= 3:
            return f"No page changes for {self.consecutive_no_progress} consecutive steps"
        
        return "Unknown stagnation"


class TaskCompletion(BaseModel):
    task_description: str = ""
    extracted_content: str
    
    def is_complete(self) -> bool:
        if not self.extracted_content or len(self.extracted_content.strip()) < 10:
            return False
        
        content = self.extracted_content.strip()
        task_lower = self.task_description.lower()
        content_lower = content.lower()
        
        strict_failures = [
            content_lower == "information not found",
            content_lower == "not found",
            len(content) < 20 and "error" in content_lower,
            len(content) < 20 and "failed" in content_lower,
        ]
        
        if any(strict_failures):
            return False
        
        if len(content) > 150:
            return True
        
        try:
            import json
            data = json.loads(self.extracted_content)
            if isinstance(data, dict):
                if "error" in data and data.get("error"):
                    return False
                
                non_empty_fields = sum(
                    1 for v in data.values() 
                    if v and str(v).strip() and str(v).lower() not in ['none', 'null', 'n/a', 'information not found']
                )
                
                if non_empty_fields >= 2:
                    return True
                elif non_empty_fields >= 1 and len(content) > 50:
                    return True
            
            elif isinstance(data, list) and len(data) >= 1:
                return True
                
        except:
            pass
        
        task_keywords = set(task_lower.split()) - {
            'find', 'get', 'search', 'locate', 'the', 'a', 'an', 'and', 'or', 'what', 'when', 'where'
        }
        task_keywords = {kw for kw in task_keywords if len(kw) > 3}
        
        if task_keywords:
            matches = sum(1 for kw in task_keywords if kw in content_lower)
            keyword_ratio = matches / len(task_keywords)
            
            if keyword_ratio >= 0.4:
                return True
        
        if len(content) >= 80:
            has_specific_data = any([
                bool(re.search(r'\d+', content)),
                bool(re.search(r'[\$£€]\s*\d+', content)),
                bool(re.search(r'\d+(?:\.\d+)?\s*(?:star|rating)', content, re.I)),
            ])
            
            if has_specific_data:
                return True
        
        if len(content) >= 50:
            return True
        
        return False
class ExtractionResult(BaseModel):
    extracted_content: Any
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source: str = "page_content"