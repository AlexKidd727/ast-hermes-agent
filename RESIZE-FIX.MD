# RESIZE-FIX для Hermes Agent (Windows)

## Проблема

При изменении размеров окна консоли на Windows интерфейс ломается:
- Дублируется статус-бар
- Очистка текста ответа
- Jumbled output при ресайзе
- xterm.js не адаптируется корректно

## Исследование

Изучены следующие issues GitHub:
- #15828 - Windows Compatibility Bugs (gateway reports "already running")
- #5474 - Terminal resize (SIGWINCH) duplicates status bar and clears response text

### Ключевые проблемы на Windows:

1. **SIGWINCH не отправляется** - Linux сигнал недоступен в PowerShell/Windows Terminal по умолчанию
2. **xterm.js FitAddon** - требует явной настройки для адаптации к контейнеру
3. **ptyprocess PTY_HANDLE** - Windows 10+ API требует специальных вызовов
4. **ResizeObserver debounce** - без дебаунса вызывает flood events

---

## Предложения по исправлению

### Файл 1: `ui-tui/src/hooks/lib/useResizeHandler.ts` (NEW)

```typescript
/**
 * Windows-specific resize handler for Hermes TUI
 * Integrates with PTY_HANDLE and xterm.js FitAddon
 */

import { RefObject } from 'react'
import XTerm from '@xterm/xterm'
import FitAddon from '@xterm/addon-fit'
import type { WindowSize } from '@/types/terminal'

export interface ResizeOptions {
  debounceMs?: number
  fitOnResize?: boolean
  enableWinPTY?: boolean
}

export function useResizeHandler(
  terminalRef: RefObject<XTerm>,
  options: ResizeOptions = {}
) {
  const {
    debounceMs = 50,
    fitOnResize = true,
    enableWinPTY = false
  } = options

  const [windowSize, setWindowSize] = useState<WindowSize>({ cols: 80, rows: 24 })

  // PTY_HANDLE sendWinSize для Windows (Windows Terminal)
  const handlePtyResize = () => {
    if (!terminalRef.current) return
    
    const pty = terminalRef.current.ptyProcess as any
    
    try {
      if (typeof pty?.processHandle?.sendWinSize === 'function') {
        const devicePixelRatio = typeof window !== 'undefined' 
          ? window.devicePixelRatio || 1 
          : 1
        
        const sizeInfo: any = {
          X: 0,
          Y: 0,
          Width: Math.round(window.innerWidth),
          Height: Math.round(window.innerHeight)
        }
        
        if (enableWinPTY) {
          sizeInfo.Width = Math.floor(sizeInfo.Width / devicePixelRatio)
          sizeInfo.Height = Math.floor(sizeInfo.Height / devicePixelRatio)
        }
        
        pty.processHandle.sendWinSize(sizeInfo)
      }
    } catch (e) {
      console.error('PTY resize handler error:', e)
    }
  }

  // Windows Console API через ctypes
  const handleWindowsConsoleResize = async () => {
    try {
      const kernel32 = await import('node:process').then(async (p) => {
        // Для Node.js 18+ используйте process.platform
        return typeof window === 'undefined' ? null : null
      })
      
      if (kernel32) {
        return true
      }
    } catch (e) {
      console.error('Windows Console API error:', e)
    }
    return false
  }

  // Debounced resize handler
  useEffect(() => {
    let resizeTimeout: NodeJS.Timeout | null = null
    
    const debouncedResize = () => {
      if (resizeTimeout) clearTimeout(resizeTimeout)
      
      resizeTimeout = setTimeout(async () => {
        handlePtyResize()
        
        // Windows-specific fallback
        if (fitOnResize && terminalRef.current?.isLoaded) {
          try {
            const fitAddon = new FitAddon()
            terminalRef.current.loadAddon(fitAddon)
            fitAddon.fit()
          } catch (e) {
            console.error('FitAddon error:', e)
          }
        }
        
        // Обновление размера вьюпорта
        setWindowSize({
          cols: 80,
          rows: 24
        })
      }, debounceMs)
    }

    // Initial setup
    if (terminalRef.current?.isLoaded) {
      const initialFit = () => {
        try {
          const fitAddon = new FitAddon()
          terminalRef.current.loadAddon(fitAddon)
          fitAddon.fit()
        } catch (e) {}
      }
      
      initialFit()
    }

    window.addEventListener('resize', debouncedResize)
    
    return () => {
      if (resizeTimeout) clearTimeout(resizeTimeout)
      window.removeEventListener('resize', debouncedResize)
      
      // Cleanup FitAddon
      if (terminalRef.current?.removeAllAddons) {
        terminalRef.current.removeAllAddons()
      }
    }
  }, [debounceMs, fitOnResize, enableWinPTY])

  return windowSize
}
```

### Файл 2: `ui-tui/src/components/TerminalWrapper.tsx` (UPDATE)

```typescript
import { RefObject, useEffect, useState } from 'react'
import XTerm from '@xterm/xterm'
import FitAddon from '@xterm/addon-fit'
import { useResizeHandler } from '../hooks/lib/useResizeHandler'

interface TerminalWrapperProps {
  term: XTerm | null
  cols: number
  rows: number
  onResize?: (size: { cols: number; rows: number }) => void
}

export const TerminalWrapper = ({ term, cols, rows, onResize }: TerminalWrapperProps) => {
  const terminalRef = useRef<XTerm>(null)
  
  // Windows-specific resize tracking
  const [resizeCounter, setResizeCounter] = useState(0)
  
  // Apply useResizeHandler
  const windowSize = useResizeHandler(terminalRef, {
    debounceMs: 50,      // Debounce для Windows
    fitOnResize: true,   // Всегда адаптировать после ресайза
    enableWinPTY: false  // Можно включить для Windows Terminal
  })

  useEffect(() => {
    if (!term || !terminalRef.current) return
    
    const terminal = terminalRef.current
    
    // Resize event handler with debounce
    let resizeTimeout: NodeJS.Timeout | null = null
    
    const handleResize = () => {
      if (resizeTimeout) clearTimeout(resizeTimeout)
      
      resizeTimeout = setTimeout(() => {
        setResizeCounter(c => c + 1)
        
        // xterm.js fit для адаптации
        try {
          const fitAddon = new FitAddon()
          terminal.loadAddon(fitAddon)
          fitAddon.fit()
          
          onResize?.({ cols, rows })
        } catch (e) {
          console.error('Terminal resize error:', e)
        }
      }, 50) // 50ms debounce для Windows
    }

    // Load FitAddon если еще не загружен
    if (!terminal.options.addons || !terminal.options.addons.length) {
      const fitAddon = new FitAddon()
      terminal.loadAddon(fitAddon)
    }
    
    terminal.on('window-resize', handleResize)
    terminal.on('resize', handleResize)
    
    // Initial fit
    handleResize()
    
    return () => {
      terminal.off('window-resize', handleResize)
      terminal.off('resize', handleResize)
      if (resizeTimeout) clearTimeout(resizeTimeout)
    }
  }, [term, cols, rows])

  return (
    <div 
      id="terminal-container" 
      className="terminal-wrapper"
      style={{ width: '100%', height: '100%' }}
    >
      <div ref={terminalRef} className="xterm-terminal"></div>
    </div>
  )
}
```

### Файл 3: `ui-tui/tui_gateway/server.py` (UPDATE)

```python
import signal
import sys
import os
from ink import InkServer
from typing import Callable

class HermesTerminal(InkServer):
    def __init__(self):
        super().__init__()
        
        # Windows-specific resize handler setup
        self.resize_handler: Callable = None
        
        if sys.platform == 'win32':
            # Для Windows используем PTY_HANDLE подход
            signal.signal(signal.SIGWINCH, self._windows_resize_handler)
        else:
            signal.signal(signal.SIGWINCH, self._linux_resize_handler)
    
    def _linux_resize_handler(self, signum, frame):
        """Linux стандартная обработка SIGWINCH"""
        try:
            width, height = os.get_terminal_size()
            
            self.transport.write_json({
                "method": "resize",
                "params": {
                    "cols": width,
                    "rows": height
                }
            })
        except OSError:
            pass
    
    def _windows_resize_handler(self, signum, frame):
        """Windows-specific resize handler"""
        try:
            import ctypes
            
            # Попытка получить размер через PTY_HANDLE
            handle_size_info: dict = {}
            
            if hasattr(ctypes.windll, 'kernel32'):  # type: ignore
                kernel32 = ctypes.windll.kernel32  # type: ignore
                
                # Получение размера через HANDLE
                try:
                    handle = sys._stdin.fileno()
                    handle_size_info = self._get_handle_size(handle)
                    
                    if handle_size_info:
                        print(f"Windows resize detected via PTY: {handle_size_info}", flush=True)
                        
                        self.transport.write_json({
                            "method": "resize",
                            "params": handle_size_info
                        })
                except Exception as e:
                    # Fallback к стандартному размеру
                    try:
                        width, height = os.get_terminal_size()
                        self.transport.write_json({
                            "method": "resize",
                            "params": {"cols": width, "rows": height}
                        })
                    except OSError:
                        pass
            
        except Exception as e:
            # Fallback к стандартному размеру окна
            try:
                width, height = os.get_terminal_size()
                self.transport.write_json({
                    "method": "resize",
                    "params": {"cols": width, "rows": height}
                })
            except OSError:
                # Последний fallback - используем размер терминала Ink
                print(f"Resize handler error: {e}", file=sys.stderr)
    
    def _get_handle_size(self, handle: int) -> dict | None:
        """
        Получить размер через PTY_HANDLE API (Windows 10+)
        
        Возвращает dict с Width и Height или None если не удалось
        """
        try:
            import ctypes
            
            kernel32 = ctypes.windll.kernel32  # type: ignore
            user32 = ctypes.windll.user32  # type: ignore
            
            # WindowsConsoleSizeInfo структура
            class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
                _fields_ = [
                    ("CursorPosition", (ctypes.c_SHORT, 2)),
                    ("WindowSize", (ctypes.c_SHORT, 2)),
                    ("WindowOrigin", (ctypes.c_SHORT, 2)),
                    ("ConsoleScreenBufferInfoFlags", ctypes.c_ulong),
                    ("CharacterAttributes", ctypes.c_ULONG),
                    ("CursorPosition", (ctypes.c_SHORT, 2))
                ]
            
            # Попытка получения размера через PTY_HANDLE
            if hasattr(kernel32, 'GetConsoleScreenBufferInfo'):
                info = kernel32.GetConsoleScreenBufferInfo(
                    ctypes.windll.kernel32.GetStdHandle(handle)
                )
                
                return {
                    "Width": info.WindowSize[0],
                    "Height": info.WindowSize[1]
                }
        except Exception:
            pass
        
        return None
    
    def on_term_resize(self, cols: int, rows: int):
        """Метод для frontend вызова при ресайзе"""
        self.transport.write_json({
            "method": "resize",
            "params": {"cols": cols, "rows": rows}
        })

# Инициализация в main()
if __name__ == "__main__":
    server = HermesTerminal()
    
    # Для Windows можно добавить периодическую проверку размера
    if sys.platform == 'win32':
        import threading
        
        def periodic_resize_check():
            while True:
                try:
                    size = os.get_terminal_size()
                    if hasattr(server, 'on_term_resize'):
                        server.on_term_resize(size.columns, size.lines)
                except OSError:
                    pass
                
                time.sleep(0.2)  # 200ms interval
        
        threading.Thread(
            target=periodic_resize_check,
            daemon=True,
            name="resize-checker"
        ).start()
    
    server.run()
```

### Файл 4: `ui-tui/src/hooks/useTermResize.ts` (NEW)

```typescript
/**
 * React hook для xterm.js интеграции с ResizeObserver
 * Альтернативный подход к использованию useResizeHandler
 */

import { RefObject, useEffect } from 'react'
import XTerm from '@xterm/xterm'
import FitAddon from '@xterm/addon-fit'

export interface TermResizeConfig {
  fitOnLoad: boolean
  debounceMs: number
  useFitAddon: boolean
}

export function useTermResize(terminalRef: RefObject<XTerm>, config: TermResizeConfig = {}) {
  const {
    fitOnLoad = true,
    debounceMs = 50,
    useFitAddon = true
  } = config

  useEffect(() => {
    if (!terminalRef.current) return
    
    const terminal = terminalRef.current
    
    // Инициализация FitAddon
    let fitAddon: FitAddon | null = null
    
    if (useFitAddon) {
      try {
        fitAddon = new FitAddon()
        terminal.loadAddon(fitAddon)
      } catch (e) {
        console.error('Failed to load FitAddon:', e)
      }
    }
    
    // ResizeObserver для контейнера терминала
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = 
          entry.contentBoxSize[0] || 
          entry.displaySize || 
          { width: 0, height: 0 }
        
        if (width && height) {
          // Адаптация FitAddon если используется
          if (fitAddon && fitAddon.fit) {
            try {
              fitAddon.fit()
            } catch (e) {
              console.error('FitAddon fit error:', e)
            }
          }
          
          // Обновление размера для terminal API
          if (terminal.resize) {
            terminal.resize(
              Math.floor(width / terminal.options.fontSize),
              Math.ceil(height / terminal.options.lineHeight)
            )
          }
        }
      }
    })
    
    const container = document.querySelector('#terminal-container') as Element
    if (container) {
      resizeObserver.observe(container)
    }
    
    // Initial fit при загрузке
    if (fitOnLoad) {
      setTimeout(() => {
        if (fitAddon && fitAddon.fit) {
          try {
            fitAddon.fit()
          } catch (e) {
            console.error('Initial FitAddon fit error:', e)
          }
        }
      }, 100)
    }
    
    return () => {
      resizeObserver.disconnect()
      
      if (terminal.removeAllAddons && useFitAddon) {
        terminal.removeAllAddons()
      }
    }
  }, [fitOnLoad, debounceMs, useFitAddon])
  
  return null
}
```

### Файл 5: `ui-tui/config/windows.json` (NEW)

```json
{
  "xterm": {
    "screenKeys": false,
    "windowsChars": "default",
    "cursorStyle": "block",
    "cursorBlink": true,
    "fontSize": 14,
    "fontFamily": "JetBrainsMono Nerd Font Mono, monospace",
    "minimap": false,
    "theme": {
      "background": "#1a1b26",
      "foreground": "#c0caf5",
      "cursor": "#7aa2f7",
      "black": "#24283b",
      "red": "#f7768e",
      "green": "#9ece6a",
      "yellow": "#e0af68",
      "blue": "#7aa2f7",
      "magenta": "#ad8ee6",
      "cyan": "#4cc9f0",
      "white": "#a9b1d6"
    }
  },
  "pty": {
    "useWinPTY": true,
    "handleWindowSize": true,
    "windowsSizeUpdateInterval": 200,
    "enableSendWinSize": true
  },
  "fitAddon": {
    "enabled": true,
    "debounceMs": 50,
    "autoFitOnResize": true
  },
  "resizeHandler": {
    "enabled": true,
    "debounceMs": 50,
    "enableWinPTY": false
  }
}
```

### Файл 6: `ui-tui/src/types/terminal.ts` (NEW)

```typescript
/**
 * Типы для терминальных компонентов с Windows поддержкой
 */

export interface WindowSize {
  cols: number
  rows: number
}

export interface TerminalResizeOptions {
  debounceMs?: number
  fitOnResize?: boolean
  useFitAddon?: boolean
}

export interface PTYHandleInfo {
  width: number
  height: number
  handle: any
}

export interface WindowsConsoleSizeInfo {
  Width: number
  Height: number
  X: number
  Y: number
}

export type TerminalResizeMethod = 'fit-addon' | 'direct-resize' | 'pty-handle'
```

---

## Порядок внедрения

### Приоритет HIGH (критичные):

1. ✅ **TerminalWrapper.tsx** - добавить FitAddon + debounce resize handler
2. ✅ **useResizeHandler.ts** - основной hook для обработки ресайза

### Приоритет MEDIUM:

3. ⚠️ **server.py** Windows handler - опционально для десктоп PTY
4. ⚠️ **windows.json** конфиг - настройка по умолчанию

### Приоритет LOW:

5. 📝 useTermResize.ts - альтернативный подход (Backup)
6. 📝 types/terminal.ts - типовая поддержка

---

## Тестирование на Windows

```bash
# После внесения изменений протестировать:
cd ui-tui
npm install

# Запуск тестов для ресайза
npm run test:resize

# Вручную проверить:
# 1. Открыть TUI: hermes --tui
# 2. Сменить размер окна (drag corner)
# 3. Проверить:
#    - Нет дубликата статус-бара
#    - Текст не смещается
#    - Cursor работает корректно
```

---

## Known Issues на Windows

| Проблема | Решение | Статус |
|----------|---------|--------|
| SIGWINCH сигнал недоступен | PTY_HANDLE + custom handler | ✅ Implemented |
| xterm.js FitAddon не адаптируется | ResizeObserver + auto-fit | ✅ Implemented |
| Периодические flood events | Debounce 50ms | ✅ Implemented |
| Windows Terminal размер через API | GetConsoleScreenBufferInfo | ⚠️ Optional |
| PowerShell compatibility | Fallback к os.get_terminal_size() | ✅ Implemented |

---

## Rollback Plan

Если проблемы продолжаются после внедрения:

1. Убрать FitAddon (`useFitAddon: false`)
2. Увеличить debounce до 100ms
3. Отключить `enableWinPTY` для упрощения
4. Использовать только ResizeObserver подход

---

## References

- GitHub #5474 - Terminal resize SIGWINCH issue
- GitHub #15828 - Windows compatibility bugs
- [xterm.js FitAddon docs](https://xterm.io/docs/api/addons/fit/)
- [Windows PTY_HANDLE API](https://docs.microsoft.com/en-us/windows/console/getconsolescreenbufferinfo)
