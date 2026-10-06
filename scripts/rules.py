#!/usr/bin/env python3
"""按角色裁切的規則包 —— 派工文的前言 — `docs/DISPATCH-TEMPLATE.md`(D-015)。

    scripts/rules.py pack worker --model opus          # 印一份 ≤ 4 KB 的前言;產不出來就非零、不印
    scripts/rules.py pack verifier --max-bytes 6000
    scripts/rules.py inspect worker --model opus --json # 唯讀:各節 bytes / 正文單位 / 缺項
    scripts/rules.py roles                             # 認得哪幾個角色、各要哪幾節

## 為什麼不整份貼
2026-09-21 外部審查的 token 帳:「共用規矩重複載入 —— CLAUDE 要整份派工規範一起給,
角色卡卻說 prompt 只指路。」整份 `DISPATCH-TEMPLATE.md` 是一萬多個字,而一個實作者
真的會被擋到的是其中五、六節;驗證者要的又是另外幾節。**每派一次工,每個 agent 各付
一次整份的錢。**

所以這一支做三件事:
1. 從共用規矩裡**抽該角色需要的那幾節**(名單在 `WANTED`,是一張表,不是一段判斷);
2. 接上**角色卡**與**那個模型自己的記憶**;
3. 全部**壓進一個位元組上限**(預設 4 KB),砍在正文單位的邊界,砍過的那一格留一個
   `…` —— 砍掉而不說,與那一節不存在長得一樣。

## 為什麼是「節錄 + 路徑」而不是「只有路徑」
只有路徑的版本試過了(`CLAUDE.md` 的「prompt 只指路」),它解掉的是重複載入,沒有解掉
「agent 沒去讀」。節錄留住會被擋到的那幾句,路徑留給要讀全文的那一次。

## 必要項一定有正文,產不出來就不交(#74)
#74 量到的:專案清單 1,121 B + 三段固定文字 1,228 B 吃掉 4 KiB 的 57%,worker 的角色卡、
模型卡、暫存區只剩標題與「截斷」,reviewer 少五節 —— 而那一份 rc=0,派工照起。所以:
- **必要項**:角色卡、指定的模型卡、名單上的每一節;存在且有正文的本專案主卡與暫存區。
  每一項至少留一個完整的**正文單位**(段落、清單的一條、一張表的一列、一整塊 code;
  暫存區是最後一條)。標題、front matter、來源欄、截斷記號、只有連結的指路行不算正文。
- **固定成本**(標題、指路、截斷記號、固定說明)≤ `FIXED_MAX`。上限 4,096 與暫存區
  600 B(D-015 / D-021)都不動 —— 這是組成預算,不是加大總額。
- 少一節、空正文、放不下:**stdout 一個字都不印**、非零、stderr 列出缺項,並送一頁
  decision(同一個缺項只送一頁)。呼叫端不准 fallback 起 agent。
"""

import argparse
import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event  # noqa: E402  共用 repo 根

# 共用規矩住哪。**先找專用的那一份**:別的專案把通用規矩抽成
# `docs/DISPATCH-COMMON-RULES.md`,這個 repo 自己的等價檔是派工範本。
SOURCES = (os.path.join("docs", "DISPATCH-COMMON-RULES.md"),
           os.path.join("docs", "DISPATCH-TEMPLATE.md"))
DEFAULT_MAX = 4096
# 固定成本的上限(#74):標題、指路、截斷記號與固定說明加起來。以前沒有這一格,
# 固定文字吃掉 2,349 B,必要正文被擠到只剩標題。
FIXED_MAX = 1024
# 專案自己寫的暫存區在包裡的一格(D-021):**≤ 600 B**,4 KB 上限不變。
# 為什麼要有這一格:`.inbox.md` 要累到 21 行才開整理票,在那之前寫下的教訓誰都讀不到,
# 而少讀一次的代價是一輪重來。為什麼只給 600 B:塞整份的話 4 KB 包會把角色卡擠掉。
# 每一格的最後一條是必要正文,不受這 600 B 擋;600 B 管的是再往前多帶幾條。
LOCAL_INBOX_BYTES = 600
# 砍過的那一格留這一個記號;它是什麼意思寫在包頭那一行(一次),不在每一格重寫一遍。
MARK = "…"
# decision 頁綁的票:有 `AC_TICKET` 綁那一張,沒有就是這個維護識別 —— 不偽造數字票號。
RULES_TICKET = "rules"
# 第五段,**固定文字**:每個角色都帶。措辭對著 `memory.py note` 真正的用法寫 ——
# 這一段決定 agent 會不會亂寫記憶:預設不寫、三種時刻、一行一原則、model 層只能寫自己。
MEMORY_NOTE = ("## 記憶回寫(D-013)\n"
               "預設不寫;要寫一次一行 `memory.py note <層> <名> \"<原則>\" --ticket <票號> "
               "--by <角色>@<模型>`(model 層只寫自己的)。EVIDENCE「記憶」段列出寫了哪幾行,"
               "沒寫就寫「無」。")
MEMORY_NOTE_BYTES = len(MEMORY_NOTE.encode("utf-8"))
# 唯讀角色(覆核者)的那一段:它的工具白名單沒有寫檔,叫它跑 `note` 是叫它做做不到的事。
READONLY_ROLES = ("reviewer",)
READONLY_MEMORY_NOTE = ("## 記憶(D-013;唯讀角色)\n"
                        "你不寫記憶檔:值得記的原則在 REVIEW ④ 寫一行「候選記憶:<一句原則>」,"
                        "主線決定收不收;沒有就寫「無」。")
# 第六段,**固定文字**(D-017,#20):交出來的東西一產生就要是機器讀得懂的。
# 這一段不抄鍵名 —— 鍵只寫在兩處(`docs/DISPATCH-TEMPLATE.md` §8.5 與 `tickets/SCHEMA.md`),
# 抄第三份的那一天,三份會各自往不同方向漂,而漂開的那一份看起來仍然像規格。
DELIVERY_NOTE = ("## 結構化交付(D-017)\n"
                 "檔尾 `## result` + 一塊 result JSON,鍵照 `docs/DISPATCH-TEMPLATE.md` §8.5 / "
                 "`tickets/SCHEMA.md`;寫不出的留 `null`,不要編。")
DELIVERY_NOTE_BYTES = len(DELIVERY_NOTE.encode("utf-8"))
# 第一段,**固定文字**,放在標題之後(D-016,#16):產品負責人 2026-09-21 原話——
# 「這樣比較快」不是判準,先算 token。所有角色與模型都帶,讀的人第一眼就看得到。
EFFICIENCY_NOTE = "不追求快,只看效率(D-016):先算 token,快不快不是判準。"
EFFICIENCY_NOTE_BYTES = len(EFFICIENCY_NOTE.encode("utf-8"))
HEADING = re.compile(r"^(#{2,4})\s+(.*)$")
NUMBER = re.compile(r"^(\d+(?:\.\d+)*)\.?\s")

# 角色 → (角色卡, 要哪幾節, 一句話)。**表在這裡,不在提示裡**(D-001)。
WANTED = {
    "worker": ("implementer.md",
               ("1", "2", "3", "4", "5", "5.5", "5.7", "6.4", "7", "8"),
               "在副本裡實作與局部驗證,交 patch + EVIDENCE"),
    "verifier": ("verifier.md",
                 ("0.5", "2", "3", "5.5", "6.4", "8"),
                 "把票面驗收寫成回歸案例,證明案例是對的,不判 PASS/FAIL"),
    "opener": ("opener.md",
               ("0", "5.5", "8"),
               "把一句話寫成完整票面(含測試計畫)"),
    # 主線不覆核(D-022:覆核由 review.sh 派 reviewer,#91 C5)。
    "main": ("main.md",
             ("0", "5.5", "5.7", "6.5"),
             "接需求、決定順序、發版"),
    "consolidator": ("consolidator.md",
                     ("0.5", "5.5", "8"),
                     "依討論結論整理記憶,保留原則與來源"),
    # 設計 session 與覆核者 2026-09-23 補上(#29 A1;D-019 / D-022)。以前它們是
    # **唯二沒有規則包的角色** —— 而「沒有包」與「不必給規矩」在派工文上長得一樣。
    "design": ("design.md",
               ("0.5", "5.5", "6.4", "8"),
               "把設計題寫成 docs/DESIGN-<題>.md,每個取捨附未來每票省/多花"),
    "reviewer": ("reviewer.md",
                 ("0.5", "3", "5.5", "6.4", "8"),
                 "讀分支上的 code 對票面驗收,交 verdict 與逐條對照"),
}
# 專案可以在 board/config.json 的 `rules` 段改版面(同步到專案後角色卡住在
# `docs/roles/`,共用規矩的節沒有編號、只有標題):
#   "rules": {"roles_dir": "docs/roles", "models_dir": "docs/roles/model",
#             "sections": {"worker": ["工作區", "判綠", …]}}
# `sections` 的每一項可以是節號(`5.5`)或標題的一段字(子字串,第一個命中的節)。
def rules_config(root):
    try:
        data = event.config(root).get("rules") or {}
    except Exception:  # noqa: BLE001  config 壞掉就用預設版面,不要在這裡倒
        data = {}
    return data if isinstance(data, dict) else {}


def roles_dir(root):
    return rules_config(root).get("roles_dir") or os.path.join("memory", "role")


def models_dir(root):
    return rules_config(root).get("models_dir") or os.path.join("memory", "model")


def local_dir(layer):
    """專案**自己寫的**那一層,固定住 `memory/<layer>`(D-021)。

    不做成設定鍵:`memory/` 永遠是「這個 repo 自己寫的」,`roles_dir` 是「規矩從哪來」。
    兩者是同一個目錄時,這個 repo 就是正本;不同時,這個 repo 是專案,規則包兩層都疊。
    一條規則,沒有第二個欄位、沒有旗標 —— 多一個鍵就多一個要對齊的地方,而對不齊的
    那天沒有人會知道(`memory.py watched_files` 的 docstring 講過同一件事)。
    """
    return os.path.join("memory", layer)


def is_canon(root):
    """這個 repo 自己就是正本嗎 —— `roles_dir` 與 `memory/role` 指到同一個目錄。"""
    return os.path.normpath(roles_dir(root)) == os.path.normpath(local_dir("role"))


def inbox_suffix(root):
    """暫存區的副檔名,與 `memory.py` 讀同一格設定(`memory.inbox_suffix`)。"""
    try:
        conf = event.config(root).get("memory") or {}
    except Exception:  # noqa: BLE001  config 壞掉就用預設,不要在這裡倒
        conf = {}
    value = conf.get("inbox_suffix") if isinstance(conf, dict) else ""
    return value or ".inbox.md"


def project_entry(root):
    """專案備忘的**一個**速查入口,不逐檔列(#74)。

    專案層不設上限、本來就會長(`docs/MEMORY.md` 2026-09-21 補註);逐檔列路徑在 T 量到
    1,121 B,把角色卡與規矩擠掉。有 code map 就指它(#77 落地前的速查入口),沒有就指
    `memory/project/` 讓人 grep(D-013 第 4 條)。兩個都不在就不出這一行。
    """
    if os.path.exists(os.path.join(root, "docs", "CODE-MAP.md")):
        return "專案速查 `docs/CODE-MAP.md`"
    where = local_dir("project")
    if os.path.isdir(os.path.join(root, where)):
        return "專案備忘 grep `%s/`" % where
    return ""


def wanted_sections(root, role, default):
    custom = (rules_config(root).get("sections") or {}).get(role)
    if isinstance(custom, list) and custom:
        return tuple(str(x) for x in custom)
    return default


def resolve(found, key):
    """`key` 對到 `blocks()` 的哪一格:先要完全相等(節號或整個標題),再用標題子字串。"""
    if key in found:
        return key
    # 節號只准完全相等:`2` 不能因為某個標題裡有「2026」就算對上。
    if re.match(r"^\d+(?:\.\d+)*$", key):
        return None
    for name, (title, _text) in found.items():
        if key in title:
            return name
    return None


ALIASES = {"implementer": "worker", "impl": "worker", "verify": "verifier",
           "verifier": "verifier", "opener": "opener", "open": "opener",
           "main": "main", "worker": "worker", "consolidator": "consolidator",
           "整理者": "consolidator", "design": "design", "設計": "design",
           "reviewer": "reviewer", "review": "reviewer", "覆核者": "reviewer"}


def source_path(root, override=""):
    if override:
        return override if os.path.isabs(override) else os.path.join(root, override)
    for rel in SOURCES:
        path = os.path.join(root, rel)
        if os.path.exists(path):
            return path
    return os.path.join(root, SOURCES[-1])


def short_sha(root):
    done = subprocess.run(["git", "-C", root, "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True, timeout=30)
    return done.stdout.strip() if done.returncode == 0 else "unknown"


def blocks(path):
    """把一份 markdown 拆成 `{號碼或標題: (標題, 內文)}`,一個標題一段。

    **平的,不是樹的**:`### 6.4` 要能單獨被挑走,而它住在 `## 6.` 底下 ——
    照樹來挑的話,挑 6.4 會把整個 §6 拖進來,那就回到「整份貼」。
    """
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return {}
    out, key, title, body = {}, None, "", []
    for line in lines:
        found = HEADING.match(line)
        if found:
            if key is not None:
                out[key] = (title, "\n".join(body).strip("\n"))
            title = found.group(2).strip()
            number = NUMBER.match(title)
            key = number.group(1) if number else title
            body = []
            continue
        if key is not None:
            body.append(line)
    if key is not None:
        out[key] = (title, "\n".join(body).strip("\n"))
    return out


ANY_HEADING = re.compile(r"^\s{0,3}#{1,6}\s")
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
TABLE_RULE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
SOURCE_FIELD = re.compile(r"^(?:[-*+]\s+)?(?:\*\*)?(?:來源|Source)(?:\*\*)?\s*[::]", re.I)
SENTENCE_END = re.compile(r"[。!?!?][)」』)\]]*$")
POINTER_NOISE = re.compile(r"\[[^\]]*\]\([^)]*\)|`[^`]*`|全文見|詳見|參見|見|see\b|->|→")


def is_content(unit):
    """正文 = 至少一行不是來源欄、不是截斷記號、不是只有連結的指路行(#74 第 4 點)。"""
    for line in unit.splitlines():
        bare = line.strip()
        if not bare or bare.startswith(MARK) or SOURCE_FIELD.match(bare):
            continue
        rest = POINTER_NOISE.sub("", LIST_ITEM.sub("", bare, count=1))
        if re.sub(r"[\W_]+", "", rest):
            return True
    return False


def units(text):
    """把一段 markdown 切成**正文單位**:段落、清單的一條(連它的續行)、表的一列、
    一整塊 code。front matter、標題、空行不算 —— 它們是固定成本,不是正文。"""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                lines = lines[index + 1:]
                break
    out, cur, fence = [], [], False

    def flush():
        if cur:
            out.append("\n".join(cur))
            del cur[:]

    for line in lines:
        bare = line.strip()
        if fence:
            cur.append(line)
            if bare.startswith("```"):
                fence = False
                flush()
            continue
        if bare.startswith("```"):
            flush()
            cur.append(line)
            fence = True
            continue
        if not bare or ANY_HEADING.match(line):
            flush()
            continue
        if bare.startswith("|"):
            # 表的分隔列黏在上一列(表頭)後面,自己不是一個單位。
            if TABLE_RULE.match(bare) and cur:
                cur.append(line)
                continue
            flush()
            cur.append(line)
            continue
        if LIST_ITEM.match(line) or (cur and cur[-1].strip().startswith("|")):
            flush()
        cur.append(line)
        # 角色卡一行一條規矩、不空行:整張卡會變成一個段落。句子在行尾結束就在這裡切;
        # 斷在句子中間的折行(共用規矩那種)留在同一個單位,不切出半句。
        if SENTENCE_END.search(line.rstrip(" *_`")):
            flush()
    flush()
    return [one for one in out if is_content(one)]


def short_title(title):
    """節名只留第一個子句:標題算固定成本,十節的全標題在 worker 包裡是 535 B(#74 量)。"""
    text = re.sub(r"[\U0001F000-\U0001FFFF☀-➿️]", "", title)
    text = text.replace("**", "").replace("`", "")
    text = NUMBER.sub("", text, count=1)
    head = re.split(r"[::((,,]|——| — ", text, maxsplit=1)[0].strip()
    return head or text.strip()


def model_card(root, model):
    """模型記憶的檔名。路由表裡的模型帶著工具前綴(`codex:gpt-5.6-sol`),而記憶檔
    的名字只有模型那一半 —— 指著一個永遠不存在的路徑,比不指路更糟:它看起來像
    「那個模型還沒有記憶」。"""
    if not model:
        return ""
    where = models_dir(root)
    for name in (model, model.split(":")[-1]):
        rel = os.path.join(where, "%s.md" % name)
        if os.path.exists(os.path.join(root, rel)):
            return rel
    return os.path.join(where, "%s.md" % model.split(":")[-1])


def read_text(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


class Cell(object):
    """包裡的一格必要項:標題(固定成本)+ 留下的正文單位。`tail` 的那一種從尾巴留
    (暫存區:一行一條、越新越下面);`weight` 是每一輪多給幾個單位(角色卡最重)。"""

    def __init__(self, item, heading, source, text, tail=False, weight=1, inbox=False):
        self.item, self.heading, self.source = item, heading, source
        self.units = units(text)
        self.source_bytes = len(text.encode("utf-8"))
        self.tail, self.weight, self.inbox = tail, weight, inbox
        self.kept = 1

    def shown(self):
        return self.units[-self.kept:] if self.tail else self.units[:self.kept]

    def body(self):
        return "\n".join(self.shown())

    def cut(self):
        return self.kept < len(self.units)


def render(layout):
    """組出整份,回 `[(格或 None, 字)]`:None 是固定成本,其餘是那一格的正文。
    統計與輸出是**同一份** —— 分開算的那一天,兩個數字會對不起來。"""
    out = []
    for entry in layout:
        if not isinstance(entry, Cell):
            out.append((None, entry + "\n"))
            continue
        out.append((None, entry.heading + "\n"))
        if entry.cut() and entry.tail:
            out.append((None, MARK + "\n"))
        out.append((entry, entry.body()))
        out.append((None, "\n"))
        if entry.cut() and not entry.tail:
            out.append((None, MARK + "\n"))
    return out


def size(segments, owner=False):
    return sum(len(text.encode("utf-8")) for who, text in segments
               if owner is False or who is owner)


def grow(cells, layout, limit):
    """每一格先只有一個正文單位;照順序輪流多給(角色卡一輪多給幾個),放不下或暫存區
    超過 600 B 的那一格就停在那裡。**只往後接連續的單位**:跳過一條長的去塞後面短的,
    留下來的就不是原文的一段,而讀的人看不出中間少了一條。"""
    def inbox_bytes():
        return sum(len(cell.body().encode("utf-8")) for cell in cells if cell.inbox)

    growing = [cell for cell in cells if cell.cut()]
    while growing:
        for cell in list(growing):
            for _step in range(cell.weight):
                cell.kept += 1
                if (size(render(layout)) > limit
                        or (cell.inbox and inbox_bytes() > LOCAL_INBOX_BYTES)):
                    cell.kept -= 1
                    growing.remove(cell)
                    break
                if not cell.cut():
                    growing.remove(cell)
                    break


def build(root, role, model, max_bytes, override=""):
    """組規則包;回 `{"text", "stats", "problems"}`。`problems` 非空 = 這一份不准交出去。"""
    card_name, default_wanted, _one_line = WANTED[role]
    wanted = wanted_sections(root, role, default_wanted)
    path = source_path(root, override)
    rel = os.path.relpath(path, root)
    problems = []

    def problem(item, source, why, nbytes=None):
        problems.append({"item": item, "source": source, "problem": why, "bytes": nbytes})

    def required(item, heading, source, **kwargs):
        full = os.path.join(root, source)
        if not os.path.isfile(full):
            problem(item, source, "檔案不存在")
            return None
        cell = Cell(item, heading, source, read_text(full), **kwargs)
        if not cell.units:
            problem(item, source, "沒有正文(只剩標題、front matter 或指路)", cell.source_bytes)
            return None
        return cell

    def optional(item, heading, source, **kwargs):
        """本專案主卡與暫存區:不在、或在但沒有正文都不是錯;有正文就是必要項。"""
        text = read_text(os.path.join(root, source)) if source else ""
        cell = Cell(item, heading, source, text, **kwargs) if text else None
        return cell if cell is not None and cell.units else None

    card_rel = os.path.join(roles_dir(root), card_name)
    card = required("角色卡", "## 角色卡 `%s`" % card_rel, card_rel, weight=3)

    found = blocks(path)
    if not os.path.isfile(path):
        problem("共用規矩", rel, "檔案不存在")
    elif not found:
        problem("共用規矩", rel, "一節都切不出來(沒有 `##` 標題)")
    sections = []
    for num in wanted:
        hit = resolve(found, num)
        if hit is None:
            if found:
                problem("§%s" % num, rel, "共用規矩裡找不到這一節(`rules.py` 的名單與文件分岔了)")
            continue
        title, text = found[hit]
        label = "§%s" % hit if NUMBER.match(title) else "§%s" % short_title(title)
        cell = Cell(label, label, "%s §%s" % (rel, hit), text)
        if not cell.units:
            problem(label, "%s §%s" % (rel, hit), "這一節沒有正文", cell.source_bytes)
            continue
        cell.titled = "%s %s" % (label, short_title(title)) if NUMBER.match(title) else label
        sections.append(cell)

    model_rel = model_card(root, model)
    memory = required("模型卡", "## 模型記憶 `%s`" % model_rel, model_rel,
                      weight=2) if model_rel else None

    # 專案層(D-021):`roles_dir` 不是 `memory/role` 的時候,這個 repo 在讀**別人的**
    # 正本規矩,而它自己的教訓寫在 `memory/`。兩層都疊;同一個目錄時只讀一次 ——
    # 疊兩次的話角色卡會整份出現兩遍,而讀的人會以為那是兩份不同的規矩。
    local = not is_canon(root)
    suffix = inbox_suffix(root)
    model_name = os.path.basename(model_rel) if model_rel else ""
    local_card = local_memory = None
    if local:
        one = os.path.join(local_dir("role"), card_name)
        local_card = optional("本專案角色卡", "### 本專案 `%s`" % one, one, weight=2)
        if model_name:
            one = os.path.join(local_dir("model"), model_name)
            local_memory = optional("本專案模型卡", "### 本專案 `%s`" % one, one)
    # 暫存區:主檔不在也要讀得到 —— 一條寫進 inbox 還沒併檔的教訓,與併過檔的那一條
    # 一樣會擋到人。來源**不綁 `local`**:暫存區永遠住在 `memory/`(這個 repo 自己寫的),
    # 而同步刻意不帶它(D-021)—— 綁在 `local` 上的話,正本自己的 inbox 兩邊都沒有讀者。
    inboxes = {}
    for key, main_rel in (("role", os.path.join(local_dir("role"), card_name)),
                          ("model", os.path.join(local_dir("model"), model_name)
                           if model_name else "")):
        if main_rel:
            one = main_rel[:-len(".md")] + suffix
            inboxes[key] = optional("暫存 %s" % one, "### 暫存 `%s`" % one, one,
                                    tail=True, inbox=True)

    if problems:
        return {"text": "", "stats": None, "problems": problems}

    head = ["# 規則包:%s @ %s" % (role, short_sha(root)),
            EFFICIENCY_NOTE,
            "「%s」= 截斷,全文見標題的路徑。" % MARK]
    entry = project_entry(root) if local else ""
    if entry:
        head.append(entry)
    layout = head + [""]
    layout += [cell for cell in (card, local_card, inboxes.get("role")) if cell]
    layout += ["", "## 共用規矩節錄 `%s`" % rel] + sections
    tail_cells = [cell for cell in (memory, local_memory, inboxes.get("model")) if cell]
    if tail_cells:
        layout += [""] + tail_cells
    layout += ["", DELIVERY_NOTE, "",
               READONLY_MEMORY_NOTE if role in READONLY_ROLES else MEMORY_NOTE]
    # 預算順序 = 重要性順序:角色卡(你是誰)> 共用規矩(你會被擋在哪)> 模型記憶
    # (你自己踩過什麼)> 暫存區。
    cells = [cell for cell in [card, local_card] + sections + [memory, local_memory,
                                                               inboxes.get("role"),
                                                               inboxes.get("model")] if cell]

    # 節名放得下才放:先只有節號,再照名單順序一節一節補上節名,補到固定成本的上限為止
    # (補不上的那幾節只少了名字,每節的正文下限照留)。
    for cell in sections:
        cell.heading = cell.titled
        if size(render(layout), None) > FIXED_MAX:
            cell.heading = cell.item
            break
    segments = render(layout)
    fixed = size(segments, None)
    if fixed > FIXED_MAX:
        problem("固定成本", "rules.py", "標題、指路與固定說明 %d B > %d B" % (fixed, FIXED_MAX),
                fixed)
    floor = size(segments)
    if floor > max_bytes:
        worst = max(cells, key=lambda cell: len(cell.body().encode("utf-8")))
        problem(worst.item, worst.source,
                "每一項只留一個正文單位就要 %d B > 上限 %d B;最大的不可分單位在這一格(%d B)"
                % (floor, max_bytes, len(worst.body().encode("utf-8"))), floor)
    if problems:
        return {"text": "", "stats": None, "problems": problems}

    grow(cells, layout, max_bytes)
    segments = render(layout)
    text = "".join(piece for _who, piece in segments)
    stats = {
        "role": role, "model": model, "max_bytes": max_bytes,
        "total_bytes": size(segments), "fixed_bytes": size(segments, None),
        "fixed_max": FIXED_MAX,
        "sections": [{"item": cell.item, "source": cell.source, "required": True,
                      "source_bytes": cell.source_bytes,
                      "rendered_bytes": size(segments, cell),
                      "content_units": cell.kept, "source_units": len(cell.units),
                      "truncated": cell.cut()} for cell in cells],
        "truncated": [cell.source for cell in cells if cell.cut()],
        "missing": [],
    }
    return {"text": text, "stats": stats, "problems": []}


def fingerprint(root, role, model, max_bytes, problems):
    """同一個角色、模型、同一份缺項內容 = 同一個指紋:重試不重送一頁。"""
    digest = hashlib.sha256()
    digest.update(json.dumps([role, model, max_bytes], ensure_ascii=False).encode("utf-8"))
    for row in problems:
        digest.update(json.dumps([row["item"], row["source"], row["problem"]],
                                 ensure_ascii=False).encode("utf-8"))
        where = os.path.join(root, row["source"].split(" §")[0])
        try:
            with open(where, "rb") as handle:
                digest.update(handle.read())
        except OSError:
            digest.update(b"(missing)")
    return digest.hexdigest()


def post_decision(root, role, model, max_bytes, problems):
    """送一頁 decision(D-032);同一個指紋已經送過就不送。回一句給 stderr 的話。
    寫不出來不吞:回的那一句說出原因,退出碼照樣非零。"""
    try:
        import inbox
        ticket = os.environ.get("AC_TICKET") or RULES_TICKET
        run_id = "rules-%s-%s" % (role, fingerprint(root, role, model, max_bytes, problems)[:12])
        for row in inbox.read_jsonl(inbox.index_path(root)):
            if str(row.get("ticket")) == str(ticket) and row.get("run_id") == run_id:
                return "同一個缺項已經送過 decision 頁(%s),這次不重送" % row.get("page")
        what = ("規則包產不出來,沒有起任何 agent。補齊下面的缺項再重派 —— 不要用 fallback 起 agent,"
                "也不要調高上限蓋過去:\n" + "".join(
                    "- %s(`%s`):%s\n" % (row["item"], row["source"], row["problem"])
                    for row in problems))
        where = "`python3 scripts/rules.py inspect %s --model %s --json`" % (role, model or '""')
        state = "規則包產不出來(%s)" % role
        with contextlib.redirect_stdout(sys.stderr):
            inbox.publish(root, ticket, run_id, "decision", state, what, where, lambda: inbox.PAGE % {
                "ticket": ticket, "subject": inbox.subject_of(root, ticket), "state": state,
                "run_id": run_id, "kind": "decision", "at": inbox.now(), "what": what,
                "where": where,
                "note": "\n## 補充\n角色 %s、模型 %s、上限 %d B\n" % (role, model or "(沒指定)",
                                                              max_bytes)})
        return "已送 decision 頁(票 %s,%s)" % (ticket, run_id)
    except Exception as exc:  # noqa: BLE001  頁寫不出來要說出原因,不能讓它變成 rc=0
        return "decision 頁寫不出來 —— %s: %s" % (type(exc).__name__, exc)


def report(role, model, max_bytes, problems):
    sys.stderr.write("rules: %s 的規則包產不出來(模型 %s,上限 %d B)—— stdout 不印半包,"
                     "呼叫端不准起 agent\n" % (role, model or "(沒指定)", max_bytes))
    for row in problems:
        sys.stderr.write("rules:   %s `%s`:%s%s\n"
                         % (row["item"], row["source"], row["problem"],
                            "" if row["bytes"] is None else "(%d B)" % row["bytes"]))
    sys.stderr.write("rules: 修法:補上缺的章節或正文、建好指定的模型卡,或改 board/config.json 的"
                     " rules.sections;不要調高上限。逐格數字:rules.py inspect %s --model %s --json\n"
                     % (role, model or '""'))


def known_role(name):
    role = ALIASES.get(name.lower())
    if not role:
        sys.stderr.write("rules: 不認得角色 %r —— 認得的是 %s\n"
                         % (name, " / ".join(sorted(set(ALIASES)))))
    return role


# 角色 → 路由表的鍵(#87 A8):這幾個角色的模型不准低於 `routing` 那一格。其餘角色不比。
ROUTING_KEYS = {"opener": "open", "worker": "implement", "verifier": "verify",
                "main": "main", "design": "design"}


def below_floor(root, role, model):
    """`--model` 低於該角色的 routing 下限就回一句給 stderr 的話;不比 / 沒低回 ""。
    `board/config.json` 沒有 `model_tiers` 時整段不動 —— 行為與以前逐字相同。"""
    conf = event.config(root)
    tiers = conf.get("model_tiers")
    key = ROUTING_KEYS.get(role)
    if not isinstance(tiers, list) or not tiers or not key or not model:
        return ""
    floor = (conf.get("routing") or {}).get(key)
    if not floor:
        return ""
    if model not in tiers or floor not in tiers:
        sys.stderr.write("rules: %s 的模型 %s / routing.%s=%s 不在 model_tiers,不比\n"
                         % (role, model, key, floor))
        return ""
    if tiers.index(model) < tiers.index(floor):
        return ("rules: %s 的模型 %s 低於 routing 下限 %s(routing.%s)—— 不產規則包;"
                "改派 %s 或更高\n" % (role, model, floor, key, floor))
    return ""


def cmd_pack(args):
    root = event.repo_root()
    role = known_role(args.role)
    if not role:
        return 2
    low = below_floor(root, role, args.model)
    if low:
        sys.stderr.write(low)
        return 2
    done = build(root, role, args.model, args.max_bytes, args.source)
    if done["problems"]:
        report(role, args.model, args.max_bytes, done["problems"])
        sys.stderr.write("rules: %s\n" % post_decision(root, role, args.model, args.max_bytes,
                                                        done["problems"]))
        return 1
    text = done["text"]
    total = len(text.encode("utf-8"))
    if total > args.max_bytes:
        # 守衛自己要守得住:超過上限就是這一支壞了,不是「差一點」。
        sys.stderr.write("rules: 壓不到 %d bytes(現在 %d)—— 這一支自己壞了\n"
                         % (args.max_bytes, total))
        return 1
    sys.stdout.write(text)
    if args.stats:
        sys.stderr.write("rules: %s %d bytes(上限 %d;固定 %d bytes;不追求快 %d bytes;"
                         "結構化交付 %d bytes;記憶回寫 %d bytes)\n"
                         % (role, total, args.max_bytes, done["stats"]["fixed_bytes"],
                            EFFICIENCY_NOTE_BYTES, DELIVERY_NOTE_BYTES, MEMORY_NOTE_BYTES))
    return 0


def cmd_inspect(args):
    """唯讀:不發事件、不送頁。派工方與維護的人用它看每一格拿到多少。"""
    root = event.repo_root()
    role = known_role(args.role)
    if not role:
        return 2
    done = build(root, role, args.model, args.max_bytes, args.source)
    data = done["stats"] or {"role": role, "model": args.model, "max_bytes": args.max_bytes,
                             "total_bytes": None, "fixed_bytes": None, "fixed_max": FIXED_MAX,
                             "sections": [], "truncated": []}
    data["missing"] = done["problems"]
    data["ok"] = not done["problems"]
    if args.json:
        sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write("rules: %s total %s B、固定 %s B(上限 %d / %d)\n"
                         % (role, data["total_bytes"], data["fixed_bytes"], args.max_bytes,
                            FIXED_MAX))
        for row in data["sections"]:
            sys.stdout.write("  %-28s %5d / %5d B  %d/%d 單位%s\n"
                             % (row["item"], row["rendered_bytes"], row["source_bytes"],
                                row["content_units"], row["source_units"],
                                "  (截斷)" if row["truncated"] else ""))
        for row in data["missing"]:
            sys.stdout.write("  缺:%s `%s`:%s\n" % (row["item"], row["source"], row["problem"]))
    return 0 if data["ok"] else 1


def cmd_roles(args):
    root = event.repo_root()
    path = source_path(root, args.source)
    found = blocks(path)
    sys.stdout.write("rules: 共用規矩 %s(%d 節)\n"
                     % (os.path.relpath(path, root), len(found)))
    for role in sorted(WANTED):
        card, wanted, one_line = WANTED[role]
        sys.stdout.write("  %-9s %s\n" % (role, one_line))
        sys.stdout.write("            角色卡 %s;節 %s\n"
                         % (os.path.join(roles_dir(root), card),
                            "、".join("§" + x for x in wanted)))
    return 0


def main(argv):
    parser = argparse.ArgumentParser(prog="rules.py", add_help=True)
    subs = parser.add_subparsers(dest="verb")

    for verb, run in (("pack", cmd_pack), ("inspect", cmd_inspect)):
        one = subs.add_parser(verb)
        one.add_argument("role")
        one.add_argument("--model", default="")
        one.add_argument("--max-bytes", type=int, default=DEFAULT_MAX)
        one.add_argument("--source", default="")
        if verb == "pack":
            one.add_argument("--stats", action="store_true")
        else:
            one.add_argument("--json", action="store_true")
        one.set_defaults(run=run)

    roles = subs.add_parser("roles")
    roles.add_argument("--source", default="")
    roles.set_defaults(run=cmd_roles)

    args = parser.parse_args(argv)
    if not getattr(args, "run", None):
        parser.print_help()
        return 2
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
