import re
from typing import List, Dict, Tuple, Optional
from constraint_parser import ConstraintParser, Constraint


class UniversalSmartQueryBuilder:
    
    FILTER_KEYWORDS = {
        'rating', 'review', 'score', 'star', 'price', 'cost', 'under', 'over',
        'above', 'below', 'minimum', 'maximum', 'min', 'max', 'less', 'more',
        'than', 'time', 'minute', 'hour', 'calorie', 'within', 'between'
    }
    
    CONSTRAINT_PATTERNS = [
        r'rating\s*[><=]+\s*\d+(?:\.\d+)?',
        r'rating\s*(?:of|at\s+least|score)?\s*\d+(?:\.\d+)?\s*(?:\+|and\s+above|or\s+(?:higher|more|better))',
        r'\d+(?:\.\d+)?\s*(?:\+|and\s+above)?\s*(?:star|stars|rating)',
        r'(?:rating|rated|review\s+score)\s*(?:at\s+least|minimum|min)?\s*\d+(?:\.\d+)?',
        r'score\s*[><=]+\s*\d+(?:\.\d+)?',
        r'(?:under|less\s+than|below|max|maximum)\s*\$?\d+(?:\.\d{2})?',
        r'(?:over|more\s+than|above|at\s+least|minimum)\s*\$?\d+(?:\.\d{2})?',
        r'\$\d+(?:\.\d{2})?',
        r'price\s*[><=]+\s*\d+',
        r'(?:more\s+than|over|above|at\s+least|minimum)\s*\d+\s*(?:reviews?|ratings?)',
        r'\d+\+?\s*(?:reviews?|ratings?)',
        r'(?:within|under|less\s+than)\s*\d+\s*(?:minutes?|mins?|hours?|hrs?)',
        r'\d+\s*(?:minutes?|mins?|hours?|hrs?)\s*or\s*less',
        r'(?:under|less\s+than|below)\s*\d+\s*(?:calories?|cal)',
        r'\d+\s*(?:calories?|cal)\s*or\s*less'
    ]
    
    @classmethod
    def build_query_and_constraints(cls, task: str) -> Tuple[str, List[Constraint]]:
        constraints = ConstraintParser.parse_task(task)
        
        search_query = cls._extract_core_search_terms(task)
        
        return search_query, constraints
    
    @classmethod
    def _extract_core_search_terms(cls, task: str) -> str:
        task_lower = task.lower()
        
        cleaned = task_lower
        for pattern in cls.CONSTRAINT_PATTERNS:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
        
        dietary_keywords = ['vegetarian', 'vegan', 'gluten-free', 'dairy-free', 'keto', 'paleo', 'low-carb']
        dietary_terms = []
        for keyword in dietary_keywords:
            if keyword in cleaned:
                dietary_terms.append(keyword)
        
        stopwords = {
            'find', 'search', 'for', 'locate', 'get', 'show', 'give', 'provide',
            'me', 'the', 'a', 'an', 'with', 'that', 'has', 'have', 'is', 'are',
            'of', 'on', 'at', 'in', 'to', 'from', 'by', 'and', 'or', 'but',
            'check', 'list', 'display', 'view', 'see'
        }
        
        words = []
        for word in cleaned.split():
            clean_word = re.sub(r'[^a-z0-9\-]', '', word)
            if clean_word and clean_word not in stopwords and clean_word not in cls.FILTER_KEYWORDS and not clean_word.isdigit():
                if len(clean_word) > 1:
                    words.append(clean_word)
        
        core_terms = dietary_terms + words[:5]
        
        if not core_terms:
            product_patterns = [
                r'recipe\s+for\s+(?:a\s+)?([a-z\s\-]+?)(?:\s+with|\s+that|\s*$)',
                r'(?:find|search)\s+(?:for\s+)?([a-z\s\-]+?)(?:\s+with|\s+that|\s*$)',
                r'([a-z\s\-]{3,})\s+recipe',
            ]
            
            for pattern in product_patterns:
                match = re.search(pattern, task_lower)
                if match:
                    product = match.group(1).strip()
                    return product
            
            return 'search'
        
        return ' '.join(core_terms)
    
    @classmethod
    def should_use_filters(cls, constraints: List[Constraint]) -> bool:
        return len(constraints) > 0
    
    @classmethod
    def get_filter_instructions(cls, constraints: List[Constraint]) -> str:
        if not constraints:
            return ""
        
        instructions = [
            "IMPORTANT: Apply these filters using the website's filter UI (do NOT include in search text):"
        ]
        
        for constraint in constraints:
            constraint_type = constraint.type.value
            value = constraint.value
            
            if 'rating' in constraint_type or 'score' in constraint_type:
                if 'min' in constraint_type:
                    instructions.append(f"- Find rating/score filter and select {value}+ or higher")
            elif 'price' in constraint_type:
                if 'max' in constraint_type:
                    instructions.append(f"- Find price filter and set maximum to ${value}")
                elif 'min' in constraint_type:
                    instructions.append(f"- Find price filter and set minimum to ${value}")
            elif 'time' in constraint_type:
                if 'max' in constraint_type:
                    instructions.append(f"- Find time/duration filter and set maximum to {value} minutes")
            elif 'review' in constraint_type:
                if 'min' in constraint_type:
                    instructions.append(f"- Find review count filter and select {value}+ reviews")
            elif constraint_type == 'dietary':
                instructions.append(f"- Find dietary/category filter and select '{value}'")
        
        instructions.append("After applying filters, verify results match ALL constraints")
        
        return '\n'.join(instructions)