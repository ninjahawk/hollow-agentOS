#!/usr/bin/env python3
"""
Hollow — matrix rain terminal monitor
Runs in its own terminal window alongside the existing agent monitor.
Polls localhost:7777 for live agent data and encodes it as rain.

Usage:
  python matrix.py [--token TOKEN]

Controls:
  q / Ctrl-C  quit
"""

import sys, os, time, math, random, threading, argparse, json
import shutil, signal, subprocess, re

# ── ANSI helpers ───────────────────────────────────────────────────────────────
def esc(*codes): return '\033[' + ';'.join(str(c) for c in codes) + 'm'
def rgb(r, g, b): return f'\033[38;2;{r};{g};{b}m'
RESET      = '\033[0m'
HIDE_CUR   = '\033[?25l'
SHOW_CUR   = '\033[?25h'
HOME       = '\033[H'
CLEAR      = '\033[2J'
ERASE_LINE = '\033[2K'

def goto(row, col): return f'\033[{row};{col}H'

# ── Characters ─────────────────────────────────────────────────────────────────
# Full-width katakana — most modern terminals render these fine
KATAKANA = list('アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン')
NUMBERS  = list('０１２３４５６７８９')

# Fallback ASCII for terminals that can't render katakana
ASCII_CH = list('abcdefghijklmnopqrstuvwxyz0123456789@#$%&')

# ── Zone colors (r, g, b) ──────────────────────────────────────────────────────
ZONE_COLOR = [
    (30,  200, 60 ),   # analyst  — green
    (20,  180, 160),   # scout    — teal
    (120, 200, 30 ),   # builder  — amber-green
]
AGENTS = ['analyst', 'scout', 'builder']

GAP   = 2    # blank columns between zones
TRAIL = 20   # max trail length

# ── Event ticker (top N lines) ────────────────────────────────────────────────
import collections
TICKER_ROWS = 3
ticker = collections.deque([{'text': 'connecting...', 'z': 0}], maxlen=TICKER_ROWS)

def ticker_push(text, z):
    with _lock:
        ticker.appendleft({'text': text, 'z': z})

# ── Per-zone signal channels ───────────────────────────────────────────────────
# Each action type has its own channel so visual effects stay visually distinct.
zAct    = [0.0]*3   # general activity  → sustained brightness
zOllama = [0.0]*3   # thinking          → slow + white head + numbers
zShell  = [0.0]*3   # shell command     → head snaps to pure bright green
zWrite  = [0.0]*3   # file write        → head turns cyan
zDeploy = [0.0]*3   # capability live   → zone color fully saturates
zGoal   = [0.0]*3   # goal progress     → warm sustained brightness lift
zMemory = [0.0]*3   # memory op         → trail dims (scan effect)
gQuorum = [0.0]     # quorum vote       → all zones sync-flash simultaneously
zProg   = [0.0]*3   # 0-1 progress bar
_lock   = threading.Lock()

def signal_zone(z, kind):
    with _lock:
        if kind == 'shell':
            zShell[z]  = min(1.0, zShell[z]  + 0.9)
            zAct[z]    = min(1.0, zAct[z]    + 0.3)
        elif kind == 'ollama':
            zOllama[z] = min(1.0, zOllama[z] + 0.95)
        elif kind == 'memory':
            zMemory[z] = min(1.0, zMemory[z] + 0.7)
        elif kind == 'write':
            zWrite[z]  = min(1.0, zWrite[z]  + 0.9)
            zAct[z]    = min(1.0, zAct[z]    + 0.3)
        elif kind == 'deploy':
            zDeploy[z] = 1.0
            zAct[z]    = min(1.0, zAct[z]    + 0.5)
        elif kind == 'goal':
            zGoal[z]   = min(1.0, zGoal[z]   + 0.7)
            zAct[z]    = min(1.0, zAct[z]    + 0.2)
        elif kind == 'quorum':
            gQuorum[0] = 1.0

def decay():
    with _lock:
        for z in range(3):
            zAct[z]    = max(0.0, zAct[z]    - 0.025)
            zOllama[z] = max(0.0, zOllama[z] - 0.02)
            zShell[z]  = max(0.0, zShell[z]  - 0.07)
            zWrite[z]  = max(0.0, zWrite[z]  - 0.06)
            zDeploy[z] = max(0.0, zDeploy[z] - 0.04)
            zGoal[z]   = max(0.0, zGoal[z]   - 0.015)
            zMemory[z] = max(0.0, zMemory[z] - 0.09)
        gQuorum[0]     = max(0.0, gQuorum[0] - 0.05)

# ── Column state ───────────────────────────────────────────────────────────────
class Col:
    def __init__(self, x, zone, sparse):
        self.x      = x
        self.zone   = zone
        self.sparse = sparse
        self.drop   = random.uniform(-30, 0)
        self.speed  = random.uniform(1.2, 2.6)
        self.trail  = []   # list of (row, char)

def build_cols(W, H):
    usable = W - GAP * 2
    zw     = max(1, usable // 3)
    b1, b2, b3, b4 = zw, zw+GAP, zw*2+GAP, zw*2+GAP*2

    cols = []
    for i in range(W):
        if   i < b1: zone = 0
        elif i < b2: zone = -1
        elif i < b3: zone = 1
        elif i < b4: zone = -1
        else:        zone = 2

        if zone < 0:
            cols.append(None)
            continue

        bd   = min(abs(i-b1), abs(i-b2), abs(i-b3), abs(i-b4))
        cols.append(Col(i, zone, bd < 2))

    return cols, zw


# ── Render one frame into a string buffer ──────────────────────────────────────
_prev_board = {}

def render_status_lines(W):
    out = []
    with _lock:
        rows = list(ticker)
    # pad to TICKER_ROWS
    while len(rows) < TICKER_ROWS:
        rows.append({'text': '', 'z': 0})
    for i, row in enumerate(rows):
        z   = row['z']
        txt = row['text']
        C   = ZONE_COLOR[z]
        # newest at 35% brightness, fading to ~10% for oldest
        fac = max(0.10, 0.35 - i * 0.12)
        color = rgb(int(C[0]*fac), int(C[1]*fac), int(C[2]*fac))
        label = ('  ' + txt)[:W - 1].ljust(W - 1)
        out.append(goto(i + 1, 1) + color + label + RESET)
    return ''.join(out)

def render_frame(cols, zw, W, H, use_kata):
    global _prev_board
    out   = [render_status_lines(W)]
    board = {}   # (row, col) -> ansi_str

    with _lock:
        acts    = list(zAct)
        ollamas = list(zOllama)
        shells  = list(zShell)
        writes  = list(zWrite)
        deploys = list(zDeploy)
        goals   = list(zGoal)
        mems    = list(zMemory)
        quorum  = gQuorum[0]

    charset = KATAKANA if use_kata else ASCII_CH

    for col in cols:
        if col is None: continue
        z      = col.zone
        act    = acts[z]
        ollama = ollamas[z]
        shell  = shells[z]
        write  = writes[z]
        deploy = deploys[z]
        goal   = goals[z]
        mem    = mems[z]
        Cr, Cg, Cb = ZONE_COLOR[z]

        # ── Speed ─────────────────────────────────────────────────────────────
        think_slow = ollama * 0.6
        spd = col.speed * max(0.15, 0.6 + act*1.2 + shell*0.8 - think_slow)
        col.drop += spd * 0.55

        if col.drop >= H and random.random() > (0.96 if col.sparse else 0.972):
            col.drop = random.uniform(-H * 0.5, 0)
            col.trail.clear()

        # ── Charset ───────────────────────────────────────────────────────────
        # ollama → numbers, deploy → numbers briefly, else katakana
        ch_set  = NUMBERS if (ollama > 0.3 or deploy > 0.5) else charset
        head_ch = random.choice(ch_set)

        col.trail.insert(0, (int(col.drop), head_ch))
        if len(col.trail) > TRAIL:
            col.trail = col.trail[:TRAIL]

        # ── Base trail color ──────────────────────────────────────────────────
        # deploy: saturate zone color fully
        # goal: warm lift (boost red/green slightly)
        # quorum: all zones shift toward white simultaneously
        dep_boost = deploy * 200
        r = min(255, Cr + act*25 + dep_boost*0.8 + quorum*80 + goal*20)
        g = min(255, Cg + act*25 + dep_boost*0.5 + quorum*80 + goal*15)
        b = min(255, Cb + act*15 + dep_boost*0.2 + quorum*80)

        # memory: dim the trail (scan effect)
        trail_scale = max(0.3, 1.0 - mem * 0.5)

        head_base  = 0.5 + act*0.25 + deploy*0.4 + goal*0.2
        head_alpha = min(1.0, head_base + ollama*0.45)

        for t, (row, ch) in enumerate(col.trail):
            if row <= TICKER_ROWS or row >= H - 1: continue
            frac  = 1 - t / TRAIL
            alpha = head_alpha if t == 0 else frac*frac*(0.18 + act*0.1) * trail_scale
            if alpha < 0.04: continue

            if t == 0:
                # ── Head color — each action type has a distinct hue ──────────
                if shell > 0.2:
                    # shell → pure bright green snap
                    w = shell
                    fr = int(20  + (20 -20)*w)
                    fg = int(255)
                    fb = int(20  + (20 -20)*w)
                elif write > 0.2:
                    # write → cyan head
                    w = write
                    fr = int(r*(1-w) + 20*w)
                    fg = int(g*(1-w) + 220*w)
                    fb = int(b*(1-w) + 255*w)
                elif ollama > 0.2:
                    # ollama → white head
                    w = ollama
                    fr = int(r + (255-r)*w)
                    fg = int(g + (255-g)*w)
                    fb = int(b + (255-b)*w)
                elif deploy > 0.3:
                    # deploy → pure saturated zone color at full brightness
                    fr, fg, fb = int(min(255,Cr*2)), int(min(255,Cg*2)), int(min(255,Cb*2))
                elif quorum > 0.3:
                    # quorum → white across all zones
                    fr, fg, fb = 220, 255, 220
                else:
                    fr = int(r * alpha)
                    fg = int(g * alpha)
                    fb = int(b * alpha)
                board[(row, col.x)] = rgb(fr, fg, fb) + ch
            else:
                fr = int(r * alpha)
                fg = int(g * alpha)
                fb = int(b * alpha)
                if fr < 3 and fg < 3 and fb < 3: continue
                board[(row, col.x)] = rgb(fr, fg, fb) + ch

    # Erase cells from last frame that have no character this frame
    for (row, cx) in _prev_board:
        if (row, cx) not in board:
            out.append(goto(row + 1, cx + 1))
            out.append(RESET + ' ')

    # Draw current board — group by row to reduce cursor jumps
    rows_used = {}
    for (row, cx), cell in board.items():
        rows_used.setdefault(row, {})[cx] = cell

    for row in sorted(rows_used):
        prev_cx = -9
        for cx in sorted(rows_used[row]):
            if cx != prev_cx + 1:
                out.append(goto(row + 1, cx + 1))
            out.append(rows_used[row][cx])
            prev_cx = cx

    out.append(RESET)
    _prev_board = board
    return ''.join(out)

# ── Docker log streamer ────────────────────────────────────────────────────────
# Streams hollow-api container logs in real time — same source as the other terminal.

# Patterns to extract meaning from log lines
_LOG_PATTERNS = [
    # agent goal progress:  builder → goal=xxx progress=0.86  (→ is unicode u+2192)
    (re.compile(u'(analyst|scout|builder)\\s*[\u2192\\->]+\\s*goal=\\S+\\s+progress=([\\d.]+)', re.I),
     lambda m: (m.group(1).lower(), 'goal', 'progress %d%%' % int(float(m.group(2)) * 100))),

    # agent assigned task
    (re.compile(r'(analyst|scout|builder).*?assigned.*?task', re.I),
     lambda m: (m.group(1).lower(), 'goal', 'assigned new task')),

    # agent has no goals
    (re.compile(r'(analyst|scout|builder).*?no goals', re.I),
     lambda m: (m.group(1).lower(), 'memory', 'idle')),

    # ollama generate/chat (not embeddings — too noisy)
    (re.compile(r'api/(generate|chat)\b.*?200', re.I),
     lambda m: (None, 'ollama', 'thinking')),

    # quorum approved
    (re.compile(r'\[QUORUM\].*?(prop-\w+)\s+approved', re.I),
     lambda m: (None, 'deploy', 'quorum approved ' + m.group(1))),

    # capability deployed / hot-loaded
    (re.compile(r'\[DEPLOY\].*?registered as\s+[\'"]?([\w_]+)', re.I),
     lambda m: (None, 'deploy', 'deployed ' + m.group(1))),

    (re.compile(r'\[DEPLOY\].*?capability\s+[\'"]?([\w_]+)\s+approved', re.I),
     lambda m: (None, 'deploy', 'live: ' + m.group(1))),

    # shell command
    (re.compile(r'shell.*?cmd[:\s]+(.{5,60})', re.I),
     lambda m: (None, 'shell', m.group(1).strip())),

    # file write
    (re.compile(r'(wrote?|writing|fs_write|file.write).*?(/\S+)', re.I),
     lambda m: (None, 'write', m.group(2))),

    # cycle counter — low signal, keep it subtle
    (re.compile(r'Cycle (\d+):', re.I),
     lambda m: (None, 'memory', 'cycle ' + m.group(1))),

    # generic agent name mention
    (re.compile(r'\b(analyst|scout|builder)\b', re.I),
     lambda m: (m.group(1).lower(), None, None)),
]

_AGENT_Z = {'analyst': 0, 'scout': 1, 'builder': 2}

def _parse_log_line(line):
    """Return (z, kind, text) or None."""
    # Extract raw message after the log prefix  (timestamp [daemon] INFO ...)
    # Format: 2026-04-08T06:27:34 [daemon] INFO   <message>
    msg = line
    for sep in (' INFO ', ' WARNING ', ' ERROR '):
        if sep in line:
            msg = line.split(sep, 1)[1].strip()
            break

    for pattern, extractor in _LOG_PATTERNS:
        m = pattern.search(line)
        if m:
            agent, kind, _ = extractor(m)
            z = _AGENT_Z.get(agent) if agent else None
            if z is None:
                weights = [0.15 + zAct[i] for i in range(3)]
                total   = sum(weights)
                rand    = random.random() * total
                z = 0
                for i in range(3):
                    rand -= weights[i]
                    if rand <= 0:
                        z = i
                        break
            # Show the actual log message so it matches the other terminal
            return z, kind, msg[:100]
    return None

_re_deploy    = re.compile(r"registered as '?([\w_]+)'?", re.I)
_re_quorum    = re.compile(r'proposal=(prop-\w+)\s+(\w+)', re.I)
_re_progress  = re.compile(r'(analyst|scout|builder).*?goal=(\S+)\s+progress=([\d.]+)', re.I)
_re_assigned  = re.compile(r'(analyst|scout|builder).*?assigned\s+(\w[\w\s]+?)(?:\s+task)?$', re.I)
_re_cycle     = re.compile(r'Cycle (\d+):\s+(\d+) agent', re.I)
_re_metrics   = re.compile(r'uptime=(\S+)\s+cycles=(\d+)\s+completed=(\d+)', re.I)
_re_semantic  = re.compile(r'indexed workspace:\s*([\d]+) chunks', re.I)

def _interpret_line(line):
    """Return (z, kind, human_label) or None. Skips noise."""
    if 'HTTP Request' in line or 'api/embeddings' in line:
        return None

    # deployed capability
    m = _re_deploy.search(line)
    if m and 'DEPLOY' in line and 'registered' in line:
        name = m.group(1)
        return 0, 'deploy', 'deployed  ' + name.replace('_', ' ')

    # quorum vote — its own kind so all zones sync-flash
    m = _re_quorum.search(line)
    if m and 'QUORUM' in line:
        prop, verdict = m.group(1), m.group(2)
        return 0, 'quorum', 'quorum %s  %s' % (verdict, prop)

    # goal progress
    m = _re_progress.search(line)
    if m:
        agent = m.group(1).lower()
        pct   = int(float(m.group(3)) * 100)
        z     = _AGENT_Z.get(agent, 0)
        label = '%s  goal progress  %d%%' % (agent, pct)
        kind  = 'goal' if pct == 100 else 'memory'
        return z, kind, label

    # agent assigned task
    m = _re_assigned.search(line)
    if m:
        agent = m.group(1).lower()
        task  = m.group(2).strip()
        z     = _AGENT_Z.get(agent, 0)
        return z, 'goal', '%s  assigned  %s' % (agent, task)

    # cycle counter
    m = _re_cycle.search(line)
    if m:
        return 0, 'memory', 'cycle %s  —  %s agent(s) active' % (m.group(1), m.group(2))

    # metrics
    m = _re_metrics.search(line)
    if m:
        return 0, 'memory', 'uptime %s  cycles %s  completed %s' % (m.group(1), m.group(2), m.group(3))

    # semantic reindex
    m = _re_semantic.search(line)
    if m:
        return 0, 'memory', 'workspace indexed  %s chunks' % m.group(1)

    return None

LOG_PATH      = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', 'daemon.log')
TOOLS_DIR     = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'memory', 'dynamic_tools')
WORKSPACE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'workspace')

# ── Workspace overlay ──────────────────────────────────────────────────────────
def show_workspace(W, H):
    import datetime

    def rule(color=rgb(20, 60, 20)):
        return color + ('─' * W) + RESET + '\n'

    # Collect every file agents write to, across all relevant dirs
    SCAN_DIRS = [
        (TOOLS_DIR,     'tool'),
        (WORKSPACE_DIR, 'work'),
    ]
    # Also scan named sub-dirs inside workspace
    try:
        for sub in os.listdir(WORKSPACE_DIR):
            sp = os.path.join(WORKSPACE_DIR, sub)
            if os.path.isdir(sp):
                SCAN_DIRS.append((sp, 'work/' + sub))
    except Exception:
        pass

    entries = []  # (mtime, path, kind, label)
    for dirpath, kind in SCAN_DIRS:
        try:
            for fname in os.listdir(dirpath):
                if fname.startswith('__') or fname.startswith('.'):
                    continue
                fp = os.path.join(dirpath, fname)
                if not os.path.isfile(fp):
                    continue
                mt = os.path.getmtime(fp)
                entries.append((mt, fp, kind, fname))
        except Exception:
            pass

    entries.sort(key=lambda e: e[0], reverse=True)

    out = [CLEAR + HOME]
    out.append(rgb(30, 200, 60) + ('  AGENT ACTIVITY  —  everything they\'ve touched, newest first').ljust(W) + RESET + '\n')
    out.append(rule())
    out.append('\n')

    max_rows = H - 6
    shown    = 0

    for mt, fp, kind, fname in entries:
        if shown >= max_rows:
            break
        ts   = datetime.datetime.fromtimestamp(mt).strftime('%m/%d %H:%M')
        size = os.path.getsize(fp)

        if kind == 'tool':
            # Read capability name from file header
            cap_name = ''
            desc     = ''
            try:
                with open(fp, 'r', encoding='utf-8', errors='replace') as fh:
                    for line in fh:
                        if not cap_name and '# Auto-synthesized capability:' in line:
                            cap_name = line.split(':', 1)[1].strip()
                        elif not desc and '# Description:' in line:
                            desc = line.split(':', 1)[1].strip()
                        if cap_name and desc:
                            break
            except Exception:
                pass
            label = (cap_name or fname.replace('synth_', '').replace('.py', ''))[:30]
            detail = desc[:max(10, W - 48)]
            out.append(
                f'  {rgb(20,140,40)}{ts}{RESET}  '
                f'{rgb(60,100,50)}tool{RESET}  '
                f'{rgb(120,200,80)}{label:<30}{RESET}  '
                f'{rgb(50,90,45)}{detail}{RESET}\n'
            )
        else:
            tag   = kind.split('/')[-1][:8]
            label = fname[:W - 30]
            out.append(
                f'  {rgb(20,120,100)}{ts}{RESET}  '
                f'{rgb(30,100,120)}{tag:<8}{RESET}  '
                f'{rgb(80,180,140)}{label:<40}{RESET}  '
                f'{rgb(30,70,55)}{size:>7}b{RESET}\n'
            )
        shown += 1

    out.append('\n')
    out.append(rule())
    out.append(f'\n  {rgb(40,90,40)}press any key to return{RESET}\n')

    sys.stdout.write(''.join(out))
    sys.stdout.flush()

def stream_log_file():
    """Tail daemon.log directly — same source as monitor.py."""
    while True:
        try:
            with open(LOG_PATH, 'r', encoding='utf-8', errors='replace') as f:
                f.seek(0, 2)  # jump to end of file
                ticker_push('connected — reading daemon.log', 0)
                while True:
                    line = f.readline()
                    if not line:
                        time.sleep(0.1)
                        continue
                    line = line.rstrip()
                    event = _interpret_line(line)
                    if event:
                        z, kind, label = event
                        if kind:
                            signal_zone(z, kind)
                        ticker_push(label, z)
        except Exception:
            pass
        time.sleep(2)

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Hollow matrix rain terminal monitor')
    parser.add_argument('--token',    default='', help='API bearer token')
    parser.add_argument('--api',      default='http://localhost:7777', help='API base URL')
    parser.add_argument('--no-kata',  action='store_true', help='Use ASCII instead of katakana')
    parser.add_argument('--fps',      type=int, default=24, help='Target frame rate (default 24)')
    args = parser.parse_args()

    # Load token: CLI arg > config.json > hardcoded default
    token = args.token
    if not token:
        for cfg in [
            'config.json',
            os.path.join(os.path.dirname(__file__), 'config.json'),
        ]:
            try:
                with open(cfg) as f:
                    data = json.load(f)
                token = data.get('api', {}).get('token') or data.get('api_token', '')
                if token: break
            except Exception:
                pass
    if not token:
        token = 'ci-test-token-replace-in-production'

    # Terminal setup
    sys.stdout.write(HIDE_CUR + CLEAR)
    sys.stdout.flush()

    def cleanup(*_):
        sys.stdout.write(SHOW_CUR + CLEAR + HOME)
        sys.stdout.flush()
        sys.exit(0)

    signal.signal(signal.SIGINT,  cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # Start background polling
    t = threading.Thread(target=stream_log_file, daemon=True)
    t.start()

    # Non-blocking keyboard input — returns keypress char or None
    try:
        import tty, termios, select
        fd  = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        def read_key():
            r, _, _ = select.select([sys.stdin], [], [], 0)
            return sys.stdin.read(1) if r else None
        has_tty = True
    except Exception:
        # Windows fallback — use msvcrt
        try:
            import msvcrt
            def read_key():
                return msvcrt.getwch() if msvcrt.kbhit() else None
        except Exception:
            read_key = lambda: None
        has_tty = False

    W, H  = shutil.get_terminal_size((120, 36))
    cols, zw = build_cols(W, H - 1)

    frame_time = 1.0 / max(1, args.fps)
    decay_counter = 0

    try:
        while True:
            t0 = time.monotonic()

            # Resize check
            nW, nH = shutil.get_terminal_size((120, 36))
            if nW != W or nH != H:
                W, H = nW, nH
                cols, zw = build_cols(W, H - 1)
                _prev_board.clear()
                sys.stdout.write(CLEAR)

            ch = read_key()
            if ch in ('q', 'Q'):
                cleanup()
            elif ch in ('p', 'P'):
                show_workspace(W, H)
                # wait for any key before resuming
                while True:
                    k = read_key()
                    if k is not None:
                        break
                    time.sleep(0.05)
                sys.stdout.write(CLEAR)
                _prev_board.clear()

            decay_counter += 1
            if decay_counter >= 3:
                decay()
                decay_counter = 0

            frame = render_frame(cols, zw, W, H - 1, not args.no_kata)
            sys.stdout.write(frame)
            sys.stdout.flush()

            elapsed = time.monotonic() - t0
            sleep   = frame_time - elapsed
            if sleep > 0:
                time.sleep(sleep)

    finally:
        if has_tty:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        cleanup()

if __name__ == '__main__':
    main()
