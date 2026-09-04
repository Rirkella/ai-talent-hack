"""Пароли: хеширование и проверка.

Ровно две функции и ноль зависимостей. `pbkdf2_hmac` есть в стандартной
библиотеке, а тянуть `passlib` или `bcrypt` ради демо-стенда — нарушение
правила «новая зависимость только с обоснованием».

Чего здесь **нет** и почему это честно сказано вслух: нет блокировки после
серии неудачных попыток, нет срока жизни сессии, нет второго фактора и нет
сброса пароля. Это форма входа, отвечающая на вопрос «кто вы», а не рубеж
обороны. Контур защищён не паролем, а тем, что приложение не выходит в сеть:
единственный внешний адрес — локальная модель.

Формат строки хеша: ``pbkdf2_sha256$<итераций>$<соль-hex>$<хеш-hex>``.
Разбирается сам собой, версионируется числом итераций, мигрируется без
переписывания схемы.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

ALGO = "pbkdf2_sha256"
# 200 000 итераций — заметно выше минимума OWASP для sha256 и незаметно
# для человека: проверка занимает около 60 мс.
ITERATIONS = 200_000
SALT_BYTES = 16


def hash_password(password: str, *, iterations: int = ITERATIONS) -> str:
    """Хеш пароля с новой случайной солью."""
    if not password:
        raise ValueError("Пустой пароль")
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGO}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Проверка пароля против хранимой строки.

    Никогда не бросает исключений: испорченная или пустая строка хеша — это
    «пароль не подошёл», а не пятисотка. Сравнение постоянного времени, чтобы
    по длительности ответа нельзя было подбирать хеш посимвольно.
    """
    if not password or not stored:
        return False
    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$")
        if algo != ALGO:
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Транслитерация для автоматических логинов. Кириллица в поле ввода на
# защите — лишний риск раскладки, а логин печатают руками.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def make_login(name: str) -> str:
    """Логин из имени: «Ирина Соколова» → ``sokolova.i``.

    Фамилия информативнее имени, а инициал разводит однофамильцев. Если имя
    состоит из одного слова, инициала не будет — это нормально.
    """
    parts = [p for p in name.replace("-", " ").split() if p]
    if not parts:
        return ""

    def tr(word: str) -> str:
        return "".join(_TRANSLIT.get(ch, ch if ch.isalnum() else "") for ch in word.lower())

    surname = tr(parts[-1] if len(parts) > 1 else parts[0])
    initial = tr(parts[0])[:1] if len(parts) > 1 else ""
    return f"{surname}.{initial}" if initial else surname


def unique_login(name: str, taken: set[str]) -> str:
    """Логин, которого ещё нет. Однофамильцы получают числовой суффикс."""
    base = make_login(name) or "user"
    if base not in taken:
        return base
    n = 2
    while f"{base}{n}" in taken:
        n += 1
    return f"{base}{n}"
