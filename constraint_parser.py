import re
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from enum import Enum


class ConstraintType(Enum):
    PRICE_MAX = "price_max"
    PRICE_MIN = "price_min"
    RATING_MIN = "rating_min"
    RATING_MAX = "rating_max"
    TIME_MAX = "time_max"
    TIME_MIN = "time_min"
    REVIEW_MIN = "review_min"
    KEYWORD_INCLUDE = "keyword_include"
    KEYWORD_EXCLUDE = "keyword_exclude"
    DIETARY = "dietary"
    CATEGORY = "category"
    SCORE_MIN = "score_min"
    SCORE_MAX = "score_max"


@dataclass
class Constraint:
    type: ConstraintType
    value: Any
    original_text: str


class ConstraintParser:
    
    PRICE_PATTERNS = [
        (r'(?:under|less than|below|max|maximum)\s*\$?(\d+(?:\.\d{2})?)', ConstraintType.PRICE_MAX),
        (r'(?:over|more than|above|min|minimum)\s*\$?(\d+(?:\.\d{2})?)', ConstraintType.PRICE_MIN),
        (r'\$(\d+(?:\.\d{2})?)\s*or\s*less', ConstraintType.PRICE_MAX),
        (r'price\s*<\s*\$?(\d+(?:\.\d{2})?)', ConstraintType.PRICE_MAX),
        (r'price\s*>\s*\$?(\d+(?:\.\d{2})?)', ConstraintType.PRICE_MIN),
    ]
    
    RATING_PATTERNS = [
        (r'rating\s*(?:of|score|at least)?\s*(\d+(?:\.\d+)?)\s*(?:\+|and above|or (?:higher|more|better))?', ConstraintType.RATING_MIN),
        (r'(\d+(?:\.\d+)?)\s*(?:\+|and above)?\s*(?:star|stars|rating)', ConstraintType.RATING_MIN),
        (r'(?:rating|rated|review score)\s*(?:at least|minimum|min)?\s*(\d+(?:\.\d+)?)', ConstraintType.RATING_MIN),
        (r'(?:rating|rated)\s*(?:above|over|more than)\s*(\d+(?:\.\d+)?)', ConstraintType.RATING_MIN),
        (r'customer review score of\s*(\d+(?:\.\d+)?)\s*or higher', ConstraintType.RATING_MIN),
    ]
    
    SCORE_PATTERNS = [
        (r'score\s*(?:of|at least)?\s*(\d+(?:\.\d+)?)\s*or higher', ConstraintType.SCORE_MIN),
        (r'score\s*above\s*(\d+(?:\.\d+)?)', ConstraintType.SCORE_MIN),
    ]
    
    TIME_PATTERNS = [
        (r'(?:under|less than|within|max|maximum)\s*(\d+)\s*(?:min|minute|minutes|hour|hours)', ConstraintType.TIME_MAX),
        (r'(\d+)\s*(?:min|minute|minutes|hour|hours)?\s*or\s*less', ConstraintType.TIME_MAX),
        (r'(?:cook|cooking|prep|preparation|ready)\s*(?:in|within)?\s*(\d+)\s*(?:min|minute|minutes)', ConstraintType.TIME_MAX),
        (r'quick\s*(\d+)\s*(?:min|minute|minutes)', ConstraintType.TIME_MAX),
    ]
    
    REVIEW_PATTERNS = [
        (r'(?:more than|over|above|at least|minimum)\s*(\d+)\s*(?:reviews?|ratings?)', ConstraintType.REVIEW_MIN),
        (r'(\d+)\+?\s*(?:reviews?|ratings?)', ConstraintType.REVIEW_MIN),
    ]
    
    DIETARY_KEYWORDS = [
        'vegetarian', 'vegan', 'gluten-free', 'dairy-free', 'keto', 'paleo',
        'low-carb', 'sugar-free', 'nut-free', 'kosher', 'halal'
    ]
    
    @classmethod
    def parse_task(cls, task: str) -> List[Constraint]:
        constraints = []
        task_lower = task.lower()
        
        for pattern, constraint_type in cls.PRICE_PATTERNS:
            matches = re.finditer(pattern, task_lower)
            for match in matches:
                constraints.append(Constraint(
                    type=constraint_type,
                    value=float(match.group(1)),
                    original_text=match.group(0)
                ))
        
        for pattern, constraint_type in cls.RATING_PATTERNS:
            matches = re.finditer(pattern, task_lower)
            for match in matches:
                constraints.append(Constraint(
                    type=constraint_type,
                    value=float(match.group(1)),
                    original_text=match.group(0)
                ))
        
        for pattern, constraint_type in cls.SCORE_PATTERNS:
            matches = re.finditer(pattern, task_lower)
            for match in matches:
                constraints.append(Constraint(
                    type=constraint_type,
                    value=float(match.group(1)),
                    original_text=match.group(0)
                ))
        
        for pattern, constraint_type in cls.TIME_PATTERNS:
            matches = re.finditer(pattern, task_lower)
            for match in matches:
                time_val = int(match.group(1))
                if 'hour' in match.group(0):
                    time_val *= 60
                constraints.append(Constraint(
                    type=constraint_type,
                    value=time_val,
                    original_text=match.group(0)
                ))
        
        for pattern, constraint_type in cls.REVIEW_PATTERNS:
            matches = re.finditer(pattern, task_lower)
            for match in matches:
                constraints.append(Constraint(
                    type=constraint_type,
                    value=int(match.group(1)),
                    original_text=match.group(0)
                ))
        
        for dietary in cls.DIETARY_KEYWORDS:
            if dietary in task_lower:
                constraints.append(Constraint(
                    type=ConstraintType.DIETARY,
                    value=dietary,
                    original_text=dietary
                ))
        
        return constraints
    
    @classmethod
    def get_constraint_summary(cls, constraints: List[Constraint]) -> str:
        """
        Get a human-readable summary of constraints
        
        Args:
            constraints: List of constraints
            
        Returns:
            Summary string like "rating > 4.5, price < $50, time < 30 min"
        """
        if not constraints:
            return "none"
        
        parts = []
        for c in constraints:
            if c.type == ConstraintType.RATING_MIN:
                parts.append(f"rating > {c.value}")
            elif c.type == ConstraintType.RATING_MAX:
                parts.append(f"rating < {c.value}")
            elif c.type == ConstraintType.SCORE_MIN:
                parts.append(f"score > {c.value}")
            elif c.type == ConstraintType.SCORE_MAX:
                parts.append(f"score < {c.value}")
            elif c.type == ConstraintType.PRICE_MIN:
                parts.append(f"price > ${c.value}")
            elif c.type == ConstraintType.PRICE_MAX:
                parts.append(f"price < ${c.value}")
            elif c.type == ConstraintType.TIME_MIN:
                parts.append(f"time > {c.value} min")
            elif c.type == ConstraintType.TIME_MAX:
                parts.append(f"time < {c.value} min")
            elif c.type == ConstraintType.REVIEW_MIN:
                parts.append(f"reviews > {c.value}")
            elif c.type == ConstraintType.DIETARY:
                parts.append(f"{c.value}")
            elif c.type == ConstraintType.CATEGORY:
                parts.append(f"category: {c.value}")
            elif c.type == ConstraintType.KEYWORD_INCLUDE:
                parts.append(f"must contain '{c.value}'")
            elif c.type == ConstraintType.KEYWORD_EXCLUDE:
                parts.append(f"must NOT contain '{c.value}'")
        
        return ", ".join(parts)
    
    @classmethod
    def format_constraints_for_prompt(cls, constraints: List[Constraint]) -> str:
        if not constraints:
            return ""
        
        lines = ["FILTERING CRITERIA - Items MUST satisfy ALL constraints:"]
        for c in constraints:
            if c.type == ConstraintType.RATING_MIN:
                lines.append(f"- Rating: {c.value}+ stars (use filter UI, not search text)")
            elif c.type == ConstraintType.SCORE_MIN:
                lines.append(f"- Score: {c.value}+ (use filter UI)")
            elif c.type == ConstraintType.REVIEW_MIN:
                lines.append(f"- Reviews: {c.value}+ minimum")
            elif c.type == ConstraintType.TIME_MAX:
                lines.append(f"- Time: under {c.value} minutes")
            elif c.type == ConstraintType.PRICE_MAX:
                lines.append(f"- Price: under ${c.value}")
            elif c.type == ConstraintType.DIETARY:
                lines.append(f"- Must be {c.value}")
        
        return "\n".join(lines)
    
    @classmethod
    def build_enhanced_search_query(cls, task: str, base_query: str = None) -> str:
        if base_query:
            return base_query.strip()
        
        task_lower = task.lower()
        
        core_items = []
        
        recipe_patterns = [
            r'(?:recipe\s+for\s+(?:a\s+)?|find\s+a\s+)([a-z\-\s]+?)(?:\s+recipe|\s+with|\s+that|\s+under|\s+over|\s+rating|\s+on)',
            r'(?:locate|search\s+for|provide)\s+(?:a\s+)?([a-z\-\s]+?)\s+recipe',
        ]
        
        for pattern in recipe_patterns:
            match = re.search(pattern, task_lower)
            if match:
                item_text = match.group(1).strip()
                
                for rating_pattern in cls.RATING_PATTERNS:
                    item_text = re.sub(rating_pattern[0], '', item_text)
                
                for dietary in cls.DIETARY_KEYWORDS:
                    if dietary in item_text:
                        if dietary not in core_items:
                            core_items.append(dietary)
                        item_text = item_text.replace(dietary, '').strip()
                
                words = [w for w in item_text.split() if w and w not in ['a', 'an', 'the', 'with', 'rating', 'score']]
                core_items.extend(words)
                break
        
        if not core_items:
            words = task_lower.split()
            skip = {'find', 'locate', 'search', 'provide', 'recipe', 'for', 'a', 'an', 'the', 'with', 'that', 'has', 'on', 'rating', 'score'}
            for word in words:
                if word not in skip and not word.isdigit() and len(word) > 2:
                    core_items.append(word)
                    if len(core_items) >= 4:
                        break
        
        return ' '.join(core_items).strip() if core_items else 'recipe'
    
    @classmethod
    def extract_form_requirements(cls, task: str) -> Dict[str, str]:
        task_lower = task.lower()
        requirements = {}
        
        if 'flight' in task_lower or 'fly' in task_lower:
            from_match = re.search(r'from ([A-Z][a-z\s]+?)(?:\s+to|\s*,)', task, re.IGNORECASE)
            if from_match:
                requirements['origin'] = from_match.group(1).strip()
            
            to_match = re.search(r'to ([A-Z][a-z\s]+?)(?:\s*,|\s+depart|\s+on|\s+with|\s+return|$)', task, re.IGNORECASE)
            if to_match:
                requirements['destination'] = to_match.group(1).strip()
            
            depart_patterns = [
                r'departing on ([A-Z][a-z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})',
                r'depart(?:ing)? on ([A-Z][a-z]+\s+\d{1,2})',
                r'on ([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})',
            ]
            for pattern in depart_patterns:
                match = re.search(pattern, task, re.IGNORECASE)
                if match:
                    requirements['departure_date'] = match.group(1).strip()
                    break
            
            return_patterns = [
                r'returning on ([A-Z][a-z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})',
                r'return(?:ing)? on ([A-Z][a-z]+\s+\d{1,2})',
            ]
            for pattern in return_patterns:
                match = re.search(pattern, task, re.IGNORECASE)
                if match:
                    requirements['return_date'] = match.group(1).strip()
                    break
            
            if 'round' in task_lower or 'round-trip' in task_lower:
                requirements['trip_type'] = 'round_trip'
            elif 'one-way' in task_lower or 'one way' in task_lower:
                requirements['trip_type'] = 'one_way'
            
            if 'nonstop' in task_lower or 'non-stop' in task_lower or 'direct' in task_lower:
                requirements['stops'] = 'nonstop'
            elif 'one stop' in task_lower or '1 stop' in task_lower:
                requirements['stops'] = '1stop'
            elif 'maximum of one stop' in task_lower:
                requirements['stops'] = '1stop'
            
            class_patterns = [
                (r'economy', 'economy'),
                (r'business', 'business'),
                (r'first class', 'first'),
                (r'premium', 'premium_economy')
            ]
            for pattern, class_name in class_patterns:
                if pattern in task_lower:
                    requirements['cabin_class'] = class_name
                    break
            
            return requirements
        
        if 'hotel' in task_lower or 'room' in task_lower or 'book' in task_lower or 'stay' in task_lower:
            location_patterns = [
                r'hotel in ([A-Za-z\s]+?)(?:\s+with|\s+for|\s+on|\s+from|,|\.|$)',
                r'hotel room in ([A-Za-z\s]+?)(?:\s+with|\s+for|\s+on|\s+from|,|\.|$)',
                r'stay\s+(?:from|on|at|in)\s+[^i]*?\s+in\s+([A-Z][a-z]+)',
                r'from\s+[^i]*?\s+in\s+([A-Z][a-z]+)',
                r'in ([A-Z][a-z]+)(?:\s*\.|,|\s+for|\s+on|\s+with)',
            ]
            
            for pattern in location_patterns:
                location_match = re.search(pattern, task, re.IGNORECASE)
                if location_match:
                    requirements['location'] = location_match.group(1).strip()
                    break
            
            date_patterns = [
                r'starting on ([A-Z][a-z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})',
                r'starting on the (\d{1,2}(?:st|nd|rd|th)?\s+[A-Z][a-z]+)',
                r'starting on ([A-Z][a-z]+\s+\d{1,2}(?:st|nd|rd|th)?)',
                r'on the (\d{1,2}(?:st|nd|rd|th)?\s+[A-Z][a-z]+)',
                r'on ([A-Z][a-z]+\s+\d{1,2}(?:st|nd|rd|th)?)',
                r'from ([A-Z][a-z]+\s+\d{1,2}(?:st|nd|rd|th)?)',
            ]
            for pattern in date_patterns:
                match = re.search(pattern, task, re.IGNORECASE)
                if match:
                    requirements['check_in_date'] = match.group(1).strip()
                    break
            
            night_match = re.search(r'(\d+)-night stay', task_lower)
            if not night_match:
                night_match = re.search(r'(one|two|three|four|five|six|seven|eight|nine|ten)-night', task_lower)
                if not night_match:
                    night_match = re.search(r'(one|two|three|four|five|six|seven|eight|nine|ten)\s+night', task_lower)
                if night_match:
                    word_to_num = {'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5',
                                   'six': '6', 'seven': '7', 'eight': '8', 'nine': '9', 'ten': '10'}
                    requirements['nights'] = word_to_num.get(night_match.group(1), '1')
            else:
                requirements['nights'] = night_match.group(1)
            
            guest_match = re.search(r'for (\d+) (?:guests?|people|adults?)', task_lower)
            if guest_match:
                requirements['guests'] = guest_match.group(1)
        
        return requirements
    
    @classmethod
    def score_item(cls, item: Dict[str, Any], constraints: List[Constraint]) -> int:
        score = 0
        for constraint in constraints:
            if constraint.type == ConstraintType.PRICE_MAX:
                price = cls._extract_price(item)
                if price is not None and price <= constraint.value:
                    score += 1
                    
            elif constraint.type == ConstraintType.PRICE_MIN:
                price = cls._extract_price(item)
                if price is not None and price >= constraint.value:
                    score += 1
                    
            elif constraint.type == ConstraintType.RATING_MIN:
                rating = cls._extract_rating(item)
                if rating is not None and rating >= constraint.value:
                    score += 1
                    
            elif constraint.type == ConstraintType.SCORE_MIN:
                score_val = cls._extract_score(item)
                if score_val is not None and score_val >= constraint.value:
                    score += 1
                    
            elif constraint.type == ConstraintType.TIME_MAX:
                time_mins = cls._extract_time(item)
                if time_mins is not None and time_mins <= constraint.value:
                    score += 1
                    
            elif constraint.type == ConstraintType.REVIEW_MIN:
                reviews = cls._extract_review_count(item)
                if reviews is not None and reviews >= constraint.value:
                    score += 1
                    
            elif constraint.type == ConstraintType.DIETARY:
                if cls._check_dietary(item, constraint.value):
                    score += 1
                    
        return score
    
    @classmethod
    def filter_items(cls, items: List[Dict[str, Any]], constraints: List[Constraint]) -> List[Dict[str, Any]]:
        filtered = []
        for item in items:
            passes = True
            for constraint in constraints:
                if constraint.type == ConstraintType.PRICE_MAX:
                    price = cls._extract_price(item)
                    if price is not None and price > constraint.value:
                        passes = False
                        break
                        
                elif constraint.type == ConstraintType.RATING_MIN:
                    rating = cls._extract_rating(item)
                    if rating is None or rating < constraint.value:
                        passes = False
                        break
                        
                elif constraint.type == ConstraintType.SCORE_MIN:
                    score = cls._extract_score(item)
                    if score is None or score < constraint.value:
                        passes = False
                        break
                        
                elif constraint.type == ConstraintType.TIME_MAX:
                    time_mins = cls._extract_time(item)
                    if time_mins is not None and time_mins > constraint.value:
                        passes = False
                        break
                        
                elif constraint.type == ConstraintType.REVIEW_MIN:
                    reviews = cls._extract_review_count(item)
                    if reviews is None or reviews < constraint.value:
                        passes = False
                        break
                        
                elif constraint.type == ConstraintType.DIETARY:
                    if not cls._check_dietary(item, constraint.value):
                        passes = False
                        break
                        
            if passes:
                filtered.append(item)
        
        return filtered
    
    @classmethod
    def _extract_price(cls, item: Dict[str, Any]) -> Optional[float]:
        text = str(item).lower()
        price_match = re.search(r'\$(\d+(?:\.\d{2})?)', text)
        if price_match:
            return float(price_match.group(1))
        return None
    
    @classmethod
    def _extract_rating(cls, item: Dict[str, Any]) -> Optional[float]:
        if isinstance(item, dict):
            if 'rating' in item:
                try:
                    return float(item['rating'])
                except:
                    pass
        
        text = str(item).lower()
        rating_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:star|rating|out of|\/)', text)
        if rating_match:
            return float(rating_match.group(1))
        return None
    
    @classmethod
    def _extract_score(cls, item: Dict[str, Any]) -> Optional[float]:
        if isinstance(item, dict):
            if 'score' in item:
                try:
                    return float(item['score'])
                except:
                    pass
        
        text = str(item).lower()
        score_match = re.search(r'score[:\s]*(\d+(?:\.\d+)?)', text)
        if score_match:
            return float(score_match.group(1))
        return None
    
    @classmethod
    def _extract_time(cls, item: Dict[str, Any]) -> Optional[int]:
        text = str(item).lower()
        
        time_match = re.search(r'(\d+)\s*(?:hour|hr)', text)
        if time_match:
            return int(time_match.group(1)) * 60
        
        time_match = re.search(r'(\d+)\s*(?:minute|min)', text)
        if time_match:
            return int(time_match.group(1))
        
        return None
    
    @classmethod
    def _extract_review_count(cls, item: Dict[str, Any]) -> Optional[int]:
        if isinstance(item, dict):
            if 'reviews' in item or 'review_count' in item:
                try:
                    return int(item.get('reviews') or item.get('review_count'))
                except:
                    pass
        
        text = str(item).lower()
        review_match = re.search(r'(\d+)\s*(?:review|rating)', text)
        if review_match:
            return int(review_match.group(1))
        return None
    
    @classmethod
    def _check_dietary(cls, item: Dict[str, Any], dietary: str) -> bool:
        text = str(item).lower()
        return dietary.lower() in text