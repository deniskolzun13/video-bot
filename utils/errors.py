"""Иерархия ошибок пайплайна (см. ТЗ v2.0.1, раздел «Ошибки»).

Классы:
  UserError           — ошибка пользовательского ввода (показывается как есть)
  ProviderError       — внешний провайдер (LLM/TTS/видео) недоступен
  ConfigurationError  — неверная конфигурация/отсутствие ключей
  ValidationError     — результат не прошёл валидацию
  CancellationError   — задача отменена
  InternalError       — непредвиденная внутренняя ошибка
"""
from utils.cancellation import CancellationError


class AppError(Exception):
    """Базовый класс ошибок бота. message — понятное сообщение для пользователя."""

    def __init__(self, message: str, details: str = ""):
        super().__init__(message)
        self.message = message
        self.details = details


class UserError(AppError):
    """Ошибка пользователя: показываем message, не логируем traceback."""


class ProviderError(AppError):
    """Ошибка внешнего провайдера (LLM/TTS/видео). details — технические детали."""


class ConfigurationError(AppError):
    """Неверная конфигурация: отсутствуют ключи/невалидные настройки."""


class ValidationError(AppError):
    """Результат не прошёл валидацию (повреждённый файл, неверный формат)."""


class InternalError(AppError):
    """Непредвиденная внутренняя ошибка."""


__all__ = [
    "AppError",
    "UserError",
    "ProviderError",
    "ConfigurationError",
    "ValidationError",
    "CancellationError",
    "InternalError",
]