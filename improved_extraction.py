import re
import json
import logging
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

STOPWORDS = {
    'find', 'get', 'search', 'locate', 'show', 'give', 'provide', 'tell',
    'what', 'when', 'where', 'who', 'how', 'why', 'which',
    'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
    'of', 'with', 'from', 'by', 'about', 'is', 'are', 'was', 'were',
    'been', 'be', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
    'would', 'should', 'could', 'may', 'might', 'must', 'can',
    'me', 'my', 'you', 'your', 'it', 'its', 'this', 'that', 'these', 'those'
}


@dataclass
class ExtractionAttempt:
    step: int
    content: str
    confidence: float
    url: str
    page_title: str
    keywords_matched: int
    has_structured_data: bool
    extraction_reason: str
    depth_score: float = 0.0


class ImprovedExtractionValidator:
    
    @staticmethod
    def _analyze_data_depth(data: Dict) -> Tuple[float, int, int]:
        """
        Analyze depth of extracted data.
        Returns (depth_score, populated_count, empty_count)
        """
        items = data.get('items', [data])
        if not isinstance(items, list):
            items = [items]
        
        shallow_fields = {'title', 'name', 'heading', 'url', 'link', 'id'}
        medium_fields = {'rating', 'reviews', 'price', 'cost', 'date', 'author', 'category', 'tags'}
        
        shallow_count = 0
        medium_count = 0
        deep_count = 0
        empty_count = 0
        
        for item in items:
            if isinstance(item, dict):
                for key, value in item.items():
                    if key in ['strategy', 'success', 'confidence', 'match_quality', 'note']:
                        continue
                    
                    if value in [None, "", []]:
                        empty_count += 1
                        continue
                    
                    key_lower = key.lower()
                    
                    if key_lower in shallow_fields:
                        shallow_count += 1
                    elif key_lower in medium_fields:
                        medium_count += 1
                    else:
                        if isinstance(value, str) and len(value) > 30:
                            deep_count += 1
                        elif isinstance(value, list) and len(value) >= 3:
                            deep_count += 1
                        elif isinstance(value, dict) and value:
                            deep_count += 1
                        else:
                            medium_count += 1
        
        total = shallow_count + medium_count + deep_count
        if total == 0:
            return 0.0, 0, empty_count
        
        depth_score = (shallow_count * 0.2 + medium_count * 0.5 + deep_count * 1.0) / total
        populated = shallow_count + medium_count + deep_count
        
        return depth_score, populated, empty_count
    
    @staticmethod
    def _detect_task_requirements(task: str) -> Dict[str, Any]:
        """
        Detect what the task is asking for.
        Returns requirements dict.
        """
        task_lower = task.lower()
        
        reqs = {
            'needs_list': False,
            'needs_details': False,
            'min_items': 1,
            'needs_substantive_content': False
        }
        
        list_words = ['find all', 'list', 'show all', 'get all', 'search for', 'compare']
        detail_words = ['extract', 'get the', 'what is', 'details about', 'information about']
        
        reqs['needs_list'] = any(word in task_lower for word in list_words)
        reqs['needs_details'] = any(word in task_lower for word in detail_words)
        
        substantive_words = ['ingredient', 'instruction', 'step', 'description', 'content', 
                            'detail', 'information', 'specification', 'feature']
        reqs['needs_substantive_content'] = any(word in task_lower for word in substantive_words)
        
        numbers = re.findall(r'\b(\d+)\b', task)
        if numbers:
            try:
                reqs['min_items'] = max(1, int(numbers[0]))
            except:
                pass
        
        return reqs
    
    @staticmethod
    def validate_extraction(
        extracted_content: str,
        task_description: str
    ) -> Tuple[bool, float, str]:
        
        if not extracted_content:
            return False, 0.0, "Empty extraction"
        
        content = extracted_content.strip()
        task_lower = task_description.lower()
        content_lower = content.lower()
        
        hard_failures = [
            ("Too short", len(content) < 20),
            ("Error occurred", "error occurred" in content_lower and len(content) < 100),
            ("Task failed", "task failed" in content_lower and len(content) < 100),
            ("Extraction failed", "extraction failed" in content_lower and len(content) < 100),
            ("Not found only", content_lower == "information not found"),
            ("Not found short", "not found" in content_lower and len(content) < 50),
        ]
        
        for reason, condition in hard_failures:
            if condition:
                return False, 0.0, reason
        
        reqs = ImprovedExtractionValidator._detect_task_requirements(task_description)
        
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                items = data.get('items', [data])
                if not isinstance(items, list):
                    items = [items]
                
                if not items:
                    return False, 0.1, "Empty items"
                
                if reqs['needs_list'] and len(items) < reqs['min_items']:
                    return True, 0.4, f"Has {len(items)} items, may need more"
                
                depth_score, populated, empty_count = ImprovedExtractionValidator._analyze_data_depth(data)
                
                if populated == 0:
                    return False, 0.1, "No populated fields"
                
                total_fields = populated + empty_count
                empty_ratio = empty_count / total_fields if total_fields > 0 else 0
                
                if empty_ratio > 0.7:
                    return True, 0.25, "Very high empty field ratio"
                elif empty_ratio > 0.5:
                    return True, 0.35, "High empty field ratio"
                
                if depth_score < 0.3:
                    return True, max(0.25, min(0.45, depth_score * 1.5)), "Only shallow fields"
                
                base_confidence = 0.3 + (depth_score * 0.4)
                
                population_bonus = (populated / total_fields) * 0.15
                base_confidence += population_bonus
                
                if reqs['needs_substantive_content'] and depth_score < 0.6:
                    base_confidence *= 0.7
                
                task_keywords = set(task_lower.split()) - STOPWORDS
                task_keywords = {kw for kw in task_keywords if len(kw) > 3}
                
                if task_keywords:
                    matches = sum(1 for kw in task_keywords if kw in content_lower)
                    keyword_bonus = (matches / len(task_keywords)) * 0.15
                    base_confidence += keyword_bonus
                
                return True, min(base_confidence, 0.90), f"Structured data (depth: {depth_score:.2f})"
                
            elif isinstance(data, list) and len(data) >= 1:
                return True, 0.80, f"List with {len(data)} items"
                
        except json.JSONDecodeError:
            pass
        
        if len(content) > 300:
            return True, 0.85, "Substantial text content"
        elif len(content) > 150:
            return True, 0.70, "Good text content"
        
        task_keywords = set(task_lower.split()) - STOPWORDS
        task_keywords = {kw for kw in task_keywords if len(kw) > 3}
        
        if task_keywords:
            matches = sum(1 for kw in task_keywords if kw in content_lower)
            keyword_ratio = matches / len(task_keywords)
            
            if keyword_ratio >= 0.6:
                return True, 0.75, f"Strong keyword match"
            elif keyword_ratio >= 0.4:
                return True, 0.60, f"Good keyword match"
            elif keyword_ratio >= 0.2:
                return True, 0.45, f"Moderate keyword match"
        
        if len(content) >= 100:
            return True, 0.50, "Moderate content"
        
        if len(content) >= 50:
            has_numbers = bool(re.search(r'\d+', content))
            has_prices = bool(re.search(r'[\$£€]\s*\d+|USD|EUR|GBP', content))
            has_ratings = bool(re.search(r'\d+(?:\.\d+)?\s*(?:star|rating|score)', content, re.I))
            
            if has_numbers or has_prices or has_ratings:
                return True, 0.55, "Contains specific data"
            
            return True, 0.40, "Some content"
        
        return False, 0.20, "Insufficient content"
    
    @staticmethod
    def score_extraction_quality(
        extracted_content: str,
        task_description: str,
        url: str = "",
        page_title: str = ""
    ) -> float:
        
        is_valid, confidence, _ = ImprovedExtractionValidator.validate_extraction(
            extracted_content,
            task_description
        )
        
        if not is_valid:
            return confidence
        
        score = confidence
        
        task_lower = task_description.lower()
        url_lower = url.lower()
        title_lower = page_title.lower()
        
        task_keywords = set(task_lower.split()) - STOPWORDS
        task_keywords = {kw for kw in task_keywords if len(kw) > 3}
        
        if task_keywords:
            url_matches = sum(1 for kw in task_keywords if kw in url_lower)
            title_matches = sum(1 for kw in task_keywords if kw in title_lower)
            
            relevance_bonus = (url_matches + title_matches) / (len(task_keywords) * 2)
            score = min(1.0, score + relevance_bonus * 0.08)
        
        return score


class ProgressiveExtractionManager:
    
    def __init__(self, task: str, extraction_interval: int = 5):
        self.task = task
        self.extraction_interval = extraction_interval
        self.attempts: List[ExtractionAttempt] = []
        self.last_extraction_step = -extraction_interval
        self.validator = ImprovedExtractionValidator()
        self.logger = logging.getLogger(__name__)
    
    def should_try_extraction(self, current_step: int, page_url: str = "") -> bool:
        
        if current_step - self.last_extraction_step >= self.extraction_interval:
            return True
        
        if self._is_promising_page(page_url):
            if current_step - self.last_extraction_step >= 2:
                return True
        
        return False
    
    def _is_promising_page(self, url: str) -> bool:
        
        if not url:
            return False
        
        url_lower = url.lower()
        task_lower = self.task.lower()
        
        task_keywords = set(task_lower.split()) - STOPWORDS
        task_keywords = {kw for kw in task_keywords if len(kw) > 3}
        
        if not task_keywords:
            return False
        
        matches = sum(1 for kw in task_keywords if kw in url_lower)
        
        return (matches / len(task_keywords)) >= 0.25
    
    def record_extraction(
        self,
        step: int,
        content: str,
        url: str,
        page_title: str
    ) -> Optional[ExtractionAttempt]:
        
        self.last_extraction_step = step
        
        is_valid, confidence, reason = self.validator.validate_extraction(
            content,
            self.task
        )
        
        if not is_valid:
            self.logger.info(f"Step {step}: Extraction rejected - {reason}")
            return None
        
        score = self.validator.score_extraction_quality(
            content,
            self.task,
            url,
            page_title
        )
        
        task_keywords = set(self.task.lower().split()) - STOPWORDS
        task_keywords = {kw for kw in task_keywords if len(kw) > 3}
        content_lower = content.lower()
        matches = sum(1 for kw in task_keywords if kw in content_lower)
        
        has_structured = False
        depth_score = 0.0
        try:
            data = json.loads(content)
            has_structured = True
            depth_score, _, _ = self.validator._analyze_data_depth(data)
        except:
            pass
        
        attempt = ExtractionAttempt(
            step=step,
            content=content,
            confidence=score,
            url=url,
            page_title=page_title,
            keywords_matched=matches,
            has_structured_data=has_structured,
            extraction_reason=reason,
            depth_score=depth_score
        )
        
        self.attempts.append(attempt)
        
        self.logger.info(
            f"Step {step}: Extraction recorded (confidence: {score:.2f}, "
            f"depth: {depth_score:.2f}, keywords: {matches}, reason: {reason})"
        )
        
        return attempt
    
    def should_finish_early(self) -> Tuple[bool, Optional[str]]:
        
        if not self.attempts:
            return False, None
        
        best = max(self.attempts, key=lambda a: a.confidence)
        
        if best.confidence < 0.80:
            self.logger.debug(f"Best confidence {best.confidence:.2f} < 0.80 threshold")
            return False, None
        
        high_confidence_attempts = [a for a in self.attempts if a.confidence >= 0.70]
        if len(high_confidence_attempts) < 2:
            self.logger.debug(f"Only {len(high_confidence_attempts)} high-confidence attempts, need 2+")
            return False, None
        
        if best.depth_score < 0.4:
            self.logger.debug(f"Data depth {best.depth_score:.2f} too shallow")
            return False, None
        
        self.logger.info(
            f"Early finish: High confidence extraction at step {best.step} "
            f"(confidence: {best.confidence:.2f}, depth: {best.depth_score:.2f})"
        )
        return True, best.content
    
    def get_best_extraction(self) -> Optional[str]:
        
        if not self.attempts:
            return None
        
        best = max(self.attempts, key=lambda a: a.confidence)
        
        self.logger.info(
            f"Best extraction: Step {best.step}, confidence {best.confidence:.2f}, depth {best.depth_score:.2f}"
        )
        
        return best.content
    
    def get_extraction_summary(self) -> Dict[str, Any]:
        
        if not self.attempts:
            return {
                "total_attempts": 0,
                "best_confidence": 0.0,
                "best_step": None
            }
        
        best = max(self.attempts, key=lambda a: a.confidence)
        
        return {
            "total_attempts": len(self.attempts),
            "best_confidence": best.confidence,
            "best_step": best.step,
            "best_keywords_matched": best.keywords_matched,
            "has_structured_data": best.has_structured_data,
            "extraction_reason": best.extraction_reason,
            "depth_score": best.depth_score,
            "all_confidences": [a.confidence for a in self.attempts]
        }