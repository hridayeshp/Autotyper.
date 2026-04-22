# Autotyper for CodeTantra

This project adds Mac keyboard shortcuts for sending code from the clipboard into CodeTantra.

## Shortcuts

- `Cmd+Option+V` or `Ctrl+Option+V`: paste the current clipboard contents
- `Cmd+Option+T` or `Ctrl+Option+T`: type the current clipboard contents very fast
- `Cmd+Option+G` or `Ctrl+Option+G`: delayed slow line-by-line typing for CodeTantra
- `Cmd+Option+B` or `Ctrl+Option+B`: stop the autotyper

## How it works

The script reads text from the clipboard first. If the clipboard is empty, it falls back to `code.txt`.

The `G` shortcut is the safest option for CodeTantra because it waits a moment for you to focus the editor and then types the code line by line.

## Run

```bash
cd /Users/hridayeshpandit/Coding/autotyper-1
python3 main.py
```

If `python3 main.py` starts with the wrong interpreter, the script will relaunch itself with the local `.venv` automatically.
