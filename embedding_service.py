import numpy as np
import tiktoken
import json
from typing import List, Dict, Tuple
import asyncio
import aiohttp
from config import api_key, proxy_url

class EmbeddingService:
    def __init__(self):
        self.encoding = tiktoken.encoding_for_model("text-embedding-ada-002")
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
    
    async def get_embedding(self, text: str) -> List[float]:
        """Получить embedding для текста"""
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": "text-embedding-ada-002",
                "input": text
            }
            
            async with session.post(
                "https://api.openai.com/v1/embeddings",
                json=payload,
                headers=self.headers,
                proxy=proxy_url
            ) as response:
                result = await response.json()
                return result['data'][0]['embedding']
    
    def cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Вычислить косинусную схожесть"""
        vec1 = np.array(vec1)
        vec2 = np.array(vec2)
        
        dot_product = np.dot(vec1, vec2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
            
        return dot_product / (norm1 * norm2)
    
    def analyze_meal_similarity(self, current_meal: str, past_meals: List[str], embeddings_cache: Dict[str, List[float]]) -> Dict:
        """Анализировать схожесть текущего приёма пищи с прошлыми"""
        results = []
        
        # Получаем embedding для текущего приёма пищи
        current_embedding = embeddings_cache.get(current_meal)
        if not current_embedding:
            # Здесь будет async вызов, но для простоты пока заглушка
            current_embedding = [0.0] * 1536  # Размер embedding для ada-002
        
        # Сравниваем с каждым прошлым приёмом пищи
        for past_meal in past_meals[-10:]:  # Последние 10 приёмов
            past_embedding = embeddings_cache.get(past_meal)
            if past_embedding:
                similarity = self.cosine_similarity(current_embedding, past_embedding)
                results.append({
                    "meal": past_meal[:50] + "..." if len(past_meal) > 50 else past_meal,
                    "similarity": similarity
                })
        
        # Сортируем по схожести
        results.sort(key=lambda x: x['similarity'], reverse=True)
        
        return {
            "current_meal": current_meal[:100] + "..." if len(current_meal) > 100 else current_meal,
            "similarity_analysis": results[:5],  # Топ-5 самых похожих
            "average_similarity": np.mean([r['similarity'] for r in results]) if results else 0,
            "is_similar_to_past": any(r['similarity'] > 0.85 for r in results)
        }

embedding_service = EmbeddingService()