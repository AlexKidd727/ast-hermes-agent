# Hermes Agent - Development Guide

**Доработанная версия проекта от Студенников А.О.** [ast-softpro.ru](https://ast-softpro.ru)

## Особенности доработки Windows-версии
- Полностью исправлена поддержка Windows (работает корректно на Windows 10/11, WSL2)
- Работает прокси для Windows (поддержка HTTP/HTTPS прокси в терминале и браузере)
- Исправлены проблемы с путями (backslash vs forward slash)
- Оптимизирована работа на Windows подсистемах


```bash
# Prefer .venv; fall back to venv if that's what your checkout has.
source .venv/bin/activate   # or: source venv/bin/activate
```
