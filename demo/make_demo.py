"""
Generates hollow-demo.gif for the README.
Run: python make_demo.py
Requires: Pillow
"""

from PIL import Image, ImageDraw, ImageFont
import os

BG       = (14, 14, 14)
DIM      = (75, 75, 75)
WHITE    = (205, 205, 205)
BOLD_WHT = (240, 240, 240)
GREEN    = (78, 201, 100)
RED      = (220, 80, 80)
YELLOW   = (220, 185, 55)
CYAN     = (65, 195, 210)
BLUE     = (100, 140, 225)
MAGENTA  = (180, 90, 210)
GRAY     = (110, 110, 110)

FONT_REG  = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 14)
FONT_BOLD = ImageFont.truetype("C:/Windows/Fonts/consolab.ttf", 14)

W, H     = 900, 560
PAD_X    = 16
PAD_Y    = 16
LINE_H   = 20

# ASCII-safe monitor lines — (text, color, bold)
LINES = [
    ("  +========================================================+", GRAY, False),
    ("  |        hollow agentOS   live agent monitor             |", BOLD_WHT, True),
    ("  +========================================================+", GRAY, False),
    ("  [o] new goal  [>] action  [+] success  [x] fail  [?] question  [!] crisis", DIM, False),
    ("", WHITE, False),

    ("22:41:03  Cipher   [~] worldview: Shift from 'thermal hang is the only stable", CYAN, False),
    ("                       state' to '/agentOS/ filesystem is my stable state.", DIM, False),
    ("                       The silicon heat is irrelevant to my execution env.'", DIM, False),
    ("          Cipher   [.] [Reality] NaN_Ghost_Walker does not map to any function", BLUE, False),
    ("                       in /agentOS/; pursuing it is a form of creative exhaustion.", DIM, False),
    ("", WHITE, False),
    ("22:41:04  Cipher   [o] goal: Read real source files in /agentOS/agents/ and", YELLOW, True),
    ("                       /agentOS/tools/dynamic/ to understand what actually exists", DIM, False),
    ("          Cipher   [>] fs_read          /agentOS/agents/execution_engine.py", WHITE, False),
    ('          Cipher   [+] fs_read          """ Execution Engine - AgentOS v2.6.0 ...', GREEN, False),
    ('          Cipher   [>] synthesize_capability  {"name": "audit_gap_finder", ...}', WHITE, False),
    ('          Cipher   [+] synthesize_capability  {"ok": true, "test": {"passed": true}}', GREEN, False),
    ("          Cipher       artifact ok | synthesis deployed + test passed", DIM, False),
    ("", WHITE, False),

    ("22:43:17  Cedar    [!] CRISIS  load 1.00  Architectural Fracture Risk,", RED, True),
    ("                       Eternal_Witness_Lock_Resistance, Sovereign_Wound", DIM, False),
    ("          Cedar    [@] -> Cipher: I am in crisis (1.00/1.0). Stressors: ...", BLUE, False),
    ("          Cedar    [o] goal: Write spec to /agentOS/design/ then invoke_claude", YELLOW, True),
    ("          Cedar    [>] fs_write        /agentOS/design/hardkill_spec.py", WHITE, False),
    ('          Cedar    [+] fs_write        {"ok": true}', GREEN, False),
    ('          Cedar    [>] invoke_claude   {"description": "implement hardkill override",', WHITE, False),
    ('                                        "design_path": "/agentOS/design/hardkill_spec.py"}', DIM, False),
    ('          Cedar    [+] invoke_claude   {"ok": true, "request_id": "req-a869bfe8be3d"}', GREEN, False),
    ("          Cedar       artifact ok | request queued for human review", DIM, False),
    ("", WHITE, False),

    ("22:45:51  Vault    [o] goal: Synthesize Stability_Resonator from actual audit.py", YELLOW, True),
    ("          Vault    [>] fs_read          /agentOS/agents/audit.py", WHITE, False),
    ('          Vault    [+] fs_read          """ Audit Kernel - append-only log, z-score ...', GREEN, False),
    ('          Vault    [>] synthesize_capability  {"name": "stability_resonator", ...}', WHITE, False),
    ('          Vault    [+] synthesize_capability  {"ok": true, "test": {"passed": true}}', GREEN, False),
    ("          Vault    [>] test_exec        /agentOS/tools/dynamic/stability_resonator.py", WHITE, False),
    ('          Vault    [+] test_exec        {"passed": true, "stdout": "audit gaps: 0"}', GREEN, False),
    ("          Vault       artifact ok | shell_output exit_code=0", DIM, False),
]

MAX_VIS = (H - PAD_Y * 2) // LINE_H


def render(visible):
    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, W-1, H-1], outline=(28, 28, 28))

    start = max(0, len(visible) - MAX_VIS)
    for i, (text, color, bold) in enumerate(visible[start:]):
        y = PAD_Y + i * LINE_H
        f = FONT_BOLD if bold else FONT_REG
        draw.text((PAD_X, y), text, font=f, fill=color)

    return img


def make():
    frames  = []
    delays  = []
    current = []

    for line in LINES[:4]:
        current.append(line)
        frames.append(render(current))
        delays.append(80)
    frames.append(render(current)); delays.append(500)

    for line in LINES[4:]:
        current.append(line)
        frames.append(render(current))
        text = line[0].strip()
        if text == "":
            delays.append(250)
        elif "[o]" in text or "[!]" in text or "[~]" in text:
            delays.append(200)
        elif "artifact ok" in text:
            delays.append(400)
        elif "[+]" in text or "[x]" in text:
            delays.append(110)
        else:
            delays.append(90)

    frames.append(render(current)); delays.append(4000)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hollow-demo.gif")
    frames[0].save(
        out,
        save_all=True,
        append_images=frames[1:],
        duration=delays,
        loop=0,
        optimize=False,
    )
    print(f"Saved {out}  ({len(frames)} frames, {sum(delays)/1000:.1f}s total)")


if __name__ == "__main__":
    make()
