import unittest
import sys
import os

# Добавляем путь к корневой папке проекта, чтобы импортировать main
# Это необходимо, так как наш тест лежит в подпапке
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

# Импортируем тестируемую функцию из main.py
# Предполагаем, что extract_calories все еще находится в main.py
try:
    from main import extract_calories
except ImportError:
    # На случай, если функция была перемещена в другой модуль
    # Можно будет позже уточнить путь
    print("ОШИБКА: Не удалось импортировать функцию extract_calories из main.py")
    print("Проверьте путь к файлу или укажите правильный модуль.")
    # Чтобы тесты не падали с ошибкой импорта, создадим заглушку
    # В реальности нужно будет исправить импорт
    def extract_calories(text): return None


class TestExtractCalories(unittest.TestCase):
    """Тесты для функции извлечения калорий из текста."""

    def test_extract_simple_kcal(self):
        """Тест извлечения калорий из формата 'XXX ккал'."""
        text = "Примерный блюдо: 450 ккал"
        self.assertEqual(extract_calories(text), 450)

    def test_extract_with_tilde(self):
        """Тест извлечения калорий с символом ~."""
        text = "Калорийность: ~350 ккал"
        self.assertEqual(extract_calories(text), 350)

    def test_extract_with_approx(self):
        """Тест извлечения калорий с символом ≈."""
        text = "≈500 ккал"
        self.assertEqual(extract_calories(text), 500)

    def test_extract_caloricity_word(self):
        """Тест извлечения калорий после слова 'калорийность'."""
        text = "калорийность 620 ккал"
        self.assertEqual(extract_calories(text), 620)

    def test_no_calories(self):
        """Тест, когда в тексте нет упоминания калорий."""
        text = "Это просто описание блюда без цифр."
        self.assertIsNone(extract_calories(text))

    def test_empty_string(self):
        """Тест на пустой строке."""
        text = ""
        self.assertIsNone(extract_calories(text))

    def test_text_with_numbers_no_kcal(self):
        """Тест, где есть числа, но не связанные с ккал."""
        text = "Рецепт включает 2 яйца и 100 г муки."
        self.assertIsNone(extract_calories(text))

    def test_kcal_in_lowercase(self):
        """Тест на регистрозависимость (должен работать с 'ккал')."""
        text = "Всего: 700 ккал"
        self.assertEqual(extract_calories(text), 700)

    # Добавь сюда новые тесты по мере необходимости

if __name__ == '__main__':
    unittest.main()