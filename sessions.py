
import tiktoken
import sqlite3
import json
import datetime
from config import DATABASE_PATH
import numpy as np
from typing import Dict, List


class SessionStorage:
    def __init__(self):
        self.init_db()
        self.encoding = tiktoken.encoding_for_model("gpt-4o-mini")
    
    def count_tokens(self, text: str) -> int:
        return len(self.encoding.encode(text))
    
    def init_db(self):
        """Создаем таблицу для сессий с правильной структурой"""
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                telegram_id INTEGER PRIMARY KEY,
                data TEXT DEFAULT '{}',
                accepted_terms INTEGER DEFAULT 0,
                registered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_visit_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'active',
                tokens_used INTEGER DEFAULT 0
            )
        ''')
        conn.commit()
        conn.close()
    
    def get_session(self, telegram_id):
        """Получить сессию пользователя"""
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT data, accepted_terms FROM sessions WHERE telegram_id = ?',
            (telegram_id,)
        )
        result = cursor.fetchone()
        conn.close()
        
        if result:
            data = json.loads(result[0]) if result[0] else {}
            return {
                'data': data,
                'accepted_terms': bool(result[1]),
                'telegram_id': telegram_id
            }
        return None
    
    def save_session(self, telegram_id, data=None, accepted_terms=None):
        """Сохранить/обновить сессию"""
        print(f"💾 СОХРАНЕНИЕ СЕССИИ: user={telegram_id}")
        
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        current = self.get_session(telegram_id)
        current_data = current['data'] if current else {}
        
        if data:
            for key, value in data.items():
                if key == 'food_logs':
                    # ✅ ВСЕГДА ЗАМЕНЯЕМ, А НЕ ДОБАВЛЯЕМ!
                    current_data[key] = value
                    print(f"   🍽 food_logs: {len(value)} записей (ПОЛНАЯ ЗАМЕНА)")
                else:
                    current_data[key] = value
        
        data_json = json.dumps(current_data)
        
        # Получаем текущее значение accepted_terms если не указано новое
        if accepted_terms is None:
            if current:
                accepted_terms = current.get('accepted_terms', False)
            else:
                accepted_terms = False
        
        if current is None:
            cursor.execute('''
                INSERT INTO sessions 
                (telegram_id, data, accepted_terms, registered_at, last_visit_at)
                VALUES (?, ?, ?, datetime('now'), datetime('now'))
            ''', (telegram_id, data_json, 1 if accepted_terms else 0))
        else:
            if accepted_terms is not None:
                cursor.execute('''
                    UPDATE sessions 
                    SET data = ?, accepted_terms = ?, last_visit_at = datetime('now')
                    WHERE telegram_id = ?
                ''', (data_json, 1 if accepted_terms else 0, telegram_id))
            else:
                cursor.execute('''
                    UPDATE sessions 
                    SET data = ?, last_visit_at = datetime('now')
                    WHERE telegram_id = ?
                ''', (data_json, telegram_id))
        
        conn.commit()
        print(f"✅ COMMIT ВЫПОЛНЕН")
        conn.close()
        return True
    
    def save_meal_embedding(self, telegram_id: int, meal_text: str, embedding: List[float]):
        """Сохранить embedding приёма пищи"""
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        # Создаём таблицу для embeddings, если её нет
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS meal_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                meal_text TEXT,
                embedding_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (telegram_id) REFERENCES sessions (telegram_id)
            )
        ''')
        
        # Сохраняем embedding
        embedding_json = json.dumps(embedding)
        cursor.execute(
            'INSERT INTO meal_embeddings (telegram_id, meal_text, embedding_json) VALUES (?, ?, ?)',
            (telegram_id, meal_text, embedding_json)
        )
        
        # ✅ ДОБАВЛЯЕМ ПРОВЕРКУ
        conn.commit()
        
        # Проверяем, что сохранилось
        cursor.execute('SELECT COUNT(*) FROM meal_embeddings WHERE telegram_id = ?', (telegram_id,))
        count = cursor.fetchone()[0]
        print(f"📊 У пользователя {telegram_id} теперь {count} embeddings")
        
        conn.close()

    def get_meal_embeddings(self, telegram_id: int, limit: int = 10) -> List[Dict]:
        """Получить embeddings приёмов пищи пользователя"""
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT meal_text, embedding_json 
            FROM meal_embeddings 
            WHERE telegram_id = ? 
            ORDER BY created_at DESC 
            LIMIT ?
        ''', (telegram_id, limit))
        
        results = cursor.fetchall()
        conn.close()
        
        embeddings = []
        for meal_text, embedding_json in results:
            try:
                embedding = json.loads(embedding_json)
                embeddings.append({
                    "meal_text": meal_text,
                    "embedding": embedding
                })
            except:
                continue
        
        return embeddings

    def get_weight_progress(self, telegram_id: int, days: int = 7) -> Dict:
        """Получить прогресс по весу за последние N дней"""
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT data FROM sessions WHERE telegram_id = ?
        ''', (telegram_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        if not result or not result[0]:
            return {"has_data": False}
        
        try:
            user_data = json.loads(result[0])
            metrics = user_data.get('metrics', [])
            
            if not metrics:
                return {"has_data": False, "message": "Нет данных о весе"}
            
            # Сортируем по дате
            sorted_metrics = sorted(metrics, key=lambda x: x.get('date', ''))
            
            # Берем последние N записей
            recent_metrics = sorted_metrics[-days:]
            
            if len(recent_metrics) < 2:
                return {"has_data": False, "message": "Недостаточно данных для анализа"}
            
            # Анализируем прогресс
            first_weight = recent_metrics[0].get('weight')
            last_weight = recent_metrics[-1].get('weight')
            
            if not first_weight or not last_weight:
                return {"has_data": False, "message": "Отсутствуют значения веса"}
            
            weight_change = last_weight - first_weight
            weight_change_per_day = weight_change / len(recent_metrics)
            
            # Определяем тренд
            trend = "stable"
            if weight_change < -1:  # Потеря более 1 кг
                trend = "loss"
            elif weight_change > 1:  # Набор более 1 кг
                trend = "gain"
            
            return {
                "has_data": True,
                "first_weight": first_weight,
                "last_weight": last_weight,
                "weight_change": weight_change,
                "weight_change_per_day": weight_change_per_day,
                "trend": trend,
                "days_analyzed": len(recent_metrics),
                "message": self._get_progress_message(trend, weight_change)
            }
            
        except Exception as e:
            return {"has_data": False, "error": str(e)}
        
    def _get_progress_message(self, trend: str, change: float) -> str:
        """Сгенерировать сообщение о прогрессе"""
        if trend == "loss":
            return f"Отличный прогресс! Вы сбросили {abs(change):.1f} кг."
        elif trend == "gain":
            return f"Вы набрали {change:.1f} кг. Возможно стоит скорректировать питание."
        else:
            return "Вес стабилен. Если хотите изменений, попробуйте скорректировать калорийность."

# Глобальный экземпляр для использования
session_storage = SessionStorage()