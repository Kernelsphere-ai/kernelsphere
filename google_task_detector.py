import asyncio
import logging
import re
from typing import Optional, Dict, List
from datetime import datetime, timedelta

class GoogleTaskDetector:
    
    @staticmethod
    def is_google_flights_task(url: str, question: str) -> bool:
        url_match = "google.com/travel/flights" in url or "google.com/flights" in url
        question_keywords = ["flight", "fly", "airline", "ticket", "airfare"]
        question_match = any(kw in question.lower() for kw in question_keywords)
        return url_match or question_match
    
    @staticmethod
    def is_google_maps_task(url: str, question: str) -> bool:
        url_match = "google.com/maps" in url
        question_keywords = ["map", "location", "address", "directions", "route", "nearby", "restaurant", "hotel"]
        question_match = any(kw in question.lower() for kw in question_keywords)
        return url_match or question_match
    
    @staticmethod
    def extract_flight_params(question: str) -> Optional[Dict]:
        
        question_lower = question.lower()
        
        origin_patterns = [
            r"from\s+['\"]([^'\"]+)['\"]",
            r'from\s+([A-Z]{3})\s',
            r'from\s+([A-Za-z\s]+?)(?:\s+to)',
            r'leave\s+from\s+([A-Za-z\s]+)',
            r'depart(?:ing)?\s+from\s+([A-Za-z\s]+)',
        ]
        
        dest_patterns = [
            r"to\s+['\"]([^'\"]+)['\"]",
            r'to\s+([A-Z]{3})(?:\s|$)',
            r'to\s+([A-Za-z\s]+?)(?:\s+and|\s+on|\s+in|\s+for|\s*$)',
            r'arriving\s+in\s+([A-Za-z\s]+)',
        ]
        
        date_patterns = [
            r'on\s+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}\s+\d{4})',
            r'on\s+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2})',
            r'(\d{4}-\d{2}-\d{2})',
            r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})',
            r'(tomorrow|today)',
        ]
        
        params = {
            "origin": None,
            "destination": None,
            "departure_date": None,
            "return_date": None,
            "cabin_class": "economy",
            "adults": 1
        }
        
        for pattern in origin_patterns:
            match = re.search(pattern, question, re.IGNORECASE)
            if match:
                origin = match.group(1).strip()
                if len(origin) > 1:
                    params["origin"] = origin
                    break
        
        for pattern in dest_patterns:
            match = re.search(pattern, question, re.IGNORECASE)
            if match:
                dest = match.group(1).strip()
                if len(dest) > 1 and dest != params["origin"]:
                    params["destination"] = dest
                    break
        
        for pattern in date_patterns:
            match = re.search(pattern, question, re.IGNORECASE)
            if match:
                date_str = match.group(1)
                parsed = GoogleTaskDetector._parse_date(date_str)
                if parsed:
                    params["departure_date"] = parsed
                    break
        
        if "return" in question_lower or "round trip" in question_lower:
            return_patterns = [
                r'return(?:ing)?\s+on\s+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}\s+\d{4})',
                r'return(?:ing)?\s+on\s+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2})',
                r'return(?:ing)?\s+(\d{4}-\d{2}-\d{2})',
            ]
            
            for pattern in return_patterns:
                return_match = re.search(pattern, question, re.IGNORECASE)
                if return_match:
                    parsed = GoogleTaskDetector._parse_date(return_match.group(1))
                    if parsed:
                        params["return_date"] = parsed
                        break
            
            if not params["return_date"] and params["departure_date"]:
                try:
                    depart = datetime.strptime(params["departure_date"], "%Y-%m-%d")
                    params["return_date"] = (depart + timedelta(days=7)).strftime("%Y-%m-%d")
                except:
                    pass
        
        if "business" in question_lower or "business class" in question_lower:
            params["cabin_class"] = "business"
        elif "first" in question_lower or "first class" in question_lower:
            params["cabin_class"] = "first"
        elif "premium" in question_lower:
            params["cabin_class"] = "premium"
        
        adults_match = re.search(r'(\d+)\s+(?:adult|passenger|people|person)', question_lower)
        if adults_match:
            params["adults"] = int(adults_match.group(1))
        
        if not params["origin"] or not params["destination"]:
            return None
        
        if not params["departure_date"]:
            params["departure_date"] = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
        
        return params
    
    @staticmethod
    def extract_maps_params(question: str) -> Dict:
        
        question_lower = question.lower()
        
        params = {
            "query": None,
            "location": None,
            "task_type": "search"
        }
        
        if "direction" in question_lower or "route" in question_lower or "how to get" in question_lower:
            params["task_type"] = "directions"
            
            origin_match = re.search(r'from\s+([A-Za-z\s,]+?)(?:\s+to)', question, re.IGNORECASE)
            dest_match = re.search(r'to\s+([A-Za-z\s,]+?)(?:\s*$|\s+in|\s+via)', question, re.IGNORECASE)
            
            if origin_match and dest_match:
                params["origin"] = origin_match.group(1).strip()
                params["destination"] = dest_match.group(1).strip()
        
        elif "review" in question_lower:
            params["task_type"] = "reviews"
            
            place_match = re.search(r'reviews?\s+(?:for|of)\s+([A-Za-z\s,]+?)(?:\s*$|\s+in)', question, re.IGNORECASE)
            if place_match:
                params["query"] = place_match.group(1).strip()
        
        elif "nearby" in question_lower or "near" in question_lower:
            params["task_type"] = "nearby"
            
            query_match = re.search(r'(?:find|show)\s+([A-Za-z\s]+?)(?:\s+near|\s+in)', question, re.IGNORECASE)
            location_match = re.search(r'(?:near|in)\s+([A-Za-z\s,]+?)(?:\s*$)', question, re.IGNORECASE)
            
            if query_match:
                params["query"] = query_match.group(1).strip()
            if location_match:
                params["location"] = location_match.group(1).strip()
        
        else:
            params["task_type"] = "search"
            
            find_match = re.search(r'(?:find|search|locate)\s+([A-Za-z\s,]+?)(?:\s*$|\s+in)', question, re.IGNORECASE)
            if find_match:
                params["query"] = find_match.group(1).strip()
            else:
                params["query"] = question
        
        return params
    
    @staticmethod
    def _parse_date(date_str: str) -> str:
        
        date_str = date_str.lower().strip()
        
        if date_str == "today":
            return datetime.now().strftime("%Y-%m-%d")
        elif date_str == "tomorrow":
            return (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        
        try:
            if re.match(r'\d{4}-\d{2}-\d{2}', date_str):
                return date_str
            
            for fmt in [
                "%B %d %Y", "%b %d %Y",
                "%B %d, %Y", "%b %d, %Y",
                "%B %d", "%b %d"
            ]:
                try:
                    date_obj = datetime.strptime(date_str, fmt)
                    if date_obj.year == 1900:
                        date_obj = date_obj.replace(year=datetime.now().year)
                        if date_obj < datetime.now():
                            date_obj = date_obj.replace(year=datetime.now().year + 1)
                    return date_obj.strftime("%Y-%m-%d")
                except ValueError:
                    continue
        except:
            pass
        
        return None


class GoogleFlightsTaskRunner:
    
    def __init__(self, page, logger):
        self.page = page
        self.logger = logger
    
    async def run(self, question: str) -> str:
        
        from google_flights_automation import GoogleFlightsAutomation, FlightSearchParams
        
        params_dict = GoogleTaskDetector.extract_flight_params(question)
        
        if not params_dict:
            self.logger.warning(f"Could not extract flight parameters from: {question}")
            return "Could not extract origin and destination from the question. Please specify clearly."
        
        self.logger.info(f"Extracted params: {params_dict}")
        
        search_params = FlightSearchParams(
            origin=params_dict["origin"],
            destination=params_dict["destination"],
            departure_date=params_dict["departure_date"],
            return_date=params_dict.get("return_date"),
            cabin_class=params_dict.get("cabin_class", "economy"),
            adults=params_dict.get("adults", 1)
        )
        
        self.logger.info(f"Searching flights: {search_params.origin} → {search_params.destination} on {search_params.departure_date}")
        
        automation = GoogleFlightsAutomation(self.page)
        results = await automation.search_flights(search_params)
        
        if not results or (len(results) == 1 and not results[0].success):
            error = results[0].error if results else "Unknown error"
            return f"Flight search failed: {error}"
        
        answer_parts = [
            f"Found {len(results)} flights from {search_params.origin} to {search_params.destination}:"
        ]
        
        for i, flight in enumerate(results[:5], 1):
            flight_info = f"\n{i}. {flight.airline} - ${flight.price}"
            if flight.departure_time:
                flight_info += f" | Departs: {flight.departure_time}"
            if flight.arrival_time:
                flight_info += f" | Arrives: {flight.arrival_time}"
            if flight.duration:
                flight_info += f" | Duration: {flight.duration}"
            if flight.stops:
                flight_info += f" | {flight.stops}"
            answer_parts.append(flight_info)
        
        return "\n".join(answer_parts)


class GoogleMapsTaskRunner:
    
    def __init__(self, page, logger):
        self.page = page
        self.logger = logger
    
    async def run(self, question: str) -> str:
        
        from google_maps_automation import GoogleMapsAutomation
        
        params = GoogleTaskDetector.extract_maps_params(question)
        automation = GoogleMapsAutomation(self.page)
        
        if params["task_type"] == "directions":
            if not params.get("origin") or not params.get("destination"):
                return "Could not extract origin and destination for directions."
            
            self.logger.info(f"Getting directions: {params['origin']} → {params['destination']}")
            
            result = await automation.get_directions(
                origin=params["origin"],
                destination=params["destination"]
            )
            
            if not result.get("success", True):
                return f"Directions failed: {result.get('error', 'Unknown error')}"
            
            answer = f"Directions from {params['origin']} to {params['destination']}:\n"
            if result.get("distance"):
                answer += f"Distance: {result['distance']}\n"
            if result.get("duration"):
                answer += f"Duration: {result['duration']}\n"
            if result.get("routes"):
                answer += f"Routes available: {len(result['routes'])}"
            
            return answer
        
        elif params["task_type"] == "reviews":
            if not params.get("query"):
                return "Could not extract place name for reviews."
            
            self.logger.info(f"Scraping reviews for: {params['query']}")
            
            reviews = await automation.scrape_reviews(params["query"], max_reviews=10)
            
            if not reviews:
                return f"No reviews found for {params['query']}"
            
            answer = f"Reviews for {params['query']} ({len(reviews)} total):\n"
            for i, review in enumerate(reviews[:5], 1):
                answer += f"\n{i}. {review.author} - {review.rating}★"
                if review.text:
                    text_preview = review.text[:100] + "..." if len(review.text) > 100 else review.text
                    answer += f"\n   {text_preview}"
            
            return answer
        
        elif params["task_type"] == "nearby":
            if not params.get("query"):
                return "Could not extract search query."
            
            self.logger.info(f"Searching nearby: {params['query']} near {params.get('location', 'current location')}")
            
            results = await automation.search_nearby(
                query=params["query"],
                location=params.get("location"),
                max_results=10
            )
            
            if not results:
                return f"No results found for {params['query']}"
            
            answer = f"Found {len(results)} results for {params['query']}:\n"
            for i, place in enumerate(results[:5], 1):
                answer += f"\n{i}. {place.name}"
                if place.rating:
                    answer += f" - {place.rating}★"
                if place.categories:
                    answer += f" ({', '.join(place.categories)})"
                if place.address:
                    answer += f"\n   {place.address}"
            
            return answer
        
        else:
            if not params.get("query"):
                params["query"] = question
            
            self.logger.info(f"Searching place: {params['query']}")
            
            place = await automation.search_place(params["query"])
            
            if not place.success:
                return f"Search failed: {place.error}"
            
            answer = f"Place: {place.name or 'Unknown'}\n"
            if place.rating:
                answer += f"Rating: {place.rating}★"
                if place.reviews_count:
                    answer += f" ({place.reviews_count} reviews)"
                answer += "\n"
            if place.address:
                answer += f"Address: {place.address}\n"
            if place.phone:
                answer += f"Phone: {place.phone}\n"
            if place.website:
                answer += f"Website: {place.website}\n"
            if place.categories:
                answer += f"Categories: {', '.join(place.categories)}\n"
            if place.price_level:
                answer += f"Price: {place.price_level}\n"
            
            return answer.strip()