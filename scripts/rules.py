#!/usr/bin/env python3
"""按角色裁切的規則包 —— 派工文的前言 — `docs/DISPATCH-TEMPLATE.md`(D-015)。

    scripts/rules.py pack worker --model opus          # 印一份 ≤ 4 KB 的前言
    scripts/rules.py pack verifier --max-bytes 6000
    scripts/rules.py roles                             # 認得哪幾個角色、各要哪幾節

## 為什麼不整份貼
2026-09-21 外部審查的 token 帳:「共用規矩重複載入 —— CLAUDE 要整份派工規範一起給,
角色卡卻說 prompt 只指路。」整份 `DISPATCH-TEMPLATE.md` 是一萬多個字,而一個實作者
真的會被擋到的是其中五、六節;驗證者要的又是另外幾節。**每派一次工,每個 agent 各付
一次整份的錢。**

所以這一支做三件事:
1. 從共用規矩裡**抽該角色需要的那幾節**(名單在 `WANTED`,是一張表,不是一段判斷);
2. 接上**角色卡**與**那個模型自己的記憶**;
3. 全部**壓進一個位元組上限**(預設 4 KB),超過的部分砍在行邊界,並**指名砍掉的是
   哪一份的哪一節** —— 砍掉而不說,與那一節不存在長得一樣。

## 為什麼是「節錄 + 路徑」而不是「只有路徑」
只有路徑的版本試過了(`CLAUDE.md` 的「prompt 只指路」),它解掉的是重複載入,沒有解掉
「agent 沒去讀」。節錄留住會被擋到的那幾句,路徑留給要讀全文的那一次。
"""

import argparse
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
# 專案自己寫的暫存區在包裡的一格(D-021):**固定 ≤ 600 B、先扣預算**,4 KB 上限不變。
# 為什麼要有這一格:`.inbox.md` 要累到 21 行才開整理票,在那之前寫下的教訓誰都讀不到,
# 而少讀一次的代價是一輪重來。為什麼只給 600 B:塞整份的話 4 KB 包會把角色卡擠掉,
# 而砍到只剩標題與沒貼一樣。
LOCAL_INBOX_BYTES = 600
# 第五段,**固定文字、先扣預算**(與鷹架同列,不吃比例):每個角色都帶,而且
# 最後那一刀砍不到它。措辭對著 `memory.py note` 真正的用法寫 —— 這一段決定 agent
# 會不會亂寫記憶:預設不寫、三種時刻、一行一原則、model 層只能寫自己。
MEMORY_NOTE = """## 記憶回寫(D-013)
預設不寫。只在三種時刻寫,一次一行:
1 同一類錯絆你兩次以上 → `model <你的模型名>`(只准寫自己的)
2 角色卡沒講、這輪自己補的規矩 → `role <角色卡檔名>`(誰都能寫)
3 跨票都成立的專案事實 → `project <主題>`(直接進主檔)
`memory.py note <層> <名> "<一句原則>" --ticket <票號> --by <角色>@<模型>`
≤ 300 字元、只寫原則,案例用票號指路;不改主檔,超標由整理票的兩個模型併。
收工前在 EVIDENCE「記憶」段列出寫了哪幾行;沒寫就寫「無」。"""
MEMORY_NOTE_BYTES = len(MEMORY_NOTE.encode("utf-8"))
# 第六段,**固定文字、先扣預算**(D-017,#20):交出來的東西一產生就要是機器讀得懂的。
# 這一段不抄鍵名 —— 鍵只寫在兩處(`docs/DISPATCH-TEMPLATE.md` §8.5 與 `tickets/SCHEMA.md`),
# 抄第三份的那一天,三份會各自往不同方向漂,而漂開的那一份看起來仍然像規格。
DELIVERY_NOTE = """## 結構化交付(D-017)
五段散文照舊給人看;**檔尾再加一段 `## result`**,底下一塊語言標記是 result 的
fenced JSON 物件 —— auto-fix 收 patch 的同一手把它抽成
`reports/t<票號>/<run_id>/result-round<輪>.json`。
鍵與範例只有兩處:`docs/DISPATCH-TEMPLATE.md` §8.5 與 `tickets/SCHEMA.md`;照抄,
不要自己發明欄位。寫不出來的欄位**照實留空**(`null` / `[]`)不要編 ——
編一個數字進去,與量過那個數字長得一樣。"""
DELIVERY_NOTE_BYTES = len(DELIVERY_NOTE.encode("utf-8"))
# 第一段,**固定文字、先扣預算**,放在標題之後、先讀清單之前(D-016,#16):產品負責人
# 2026-09-21 原話——「這樣比較快」不是判準,先算 token。所有角色與模型都帶,量在最前面
# 的三行裡,讀的人第一眼就看得到。
EFFICIENCY_NOTE = ("不追求快,只看效率(D-016):在因為「這樣比較快」而做任何事之前,"
                   "先算 token 是多還是少;快不快不是判準。")
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
    "main": ("main.md",
             ("0", "5.5", "5.7", "6.5"),
             "接需求、覆核、決定順序、發版"),
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


def project_notes(root):
    """`memory/project/*.md` 的路徑,**只列路徑不貼內容**。

    專案層不設上限、本來就會長(`docs/MEMORY.md` 2026-09-21 補註);貼進 4 KB 包裡會把
    角色卡與規矩擠掉,而砍到只剩標題與沒貼一樣。要看的那一次用 grep(D-013 第 4 條)。
    """
    where = os.path.join(root, local_dir("project"))
    try:
        names = sorted(name for name in os.listdir(where) if name.endswith(".md"))
    except OSError:
        return []
    return [os.path.join(local_dir("project"), name) for name in names]


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


def clip(text, budget, pointer):
    """砍在行邊界,並**指名砍掉的是哪一份的哪一節**。

    砍掉而不說話,與那一節根本不存在長得一樣 —— 而讀的人會以為自己看到的是全部。
    """
    raw = text.encode("utf-8")
    if len(raw) <= budget:
        return text, False
    mark = "…(截斷:全文見 %s)" % pointer
    room = budget - len(mark.encode("utf-8")) - 1
    kept = []
    used = 0
    for line in text.splitlines():
        size = len(line.encode("utf-8")) + 1
        if used + size > room:
            break
        kept.append(line)
        used += size
    return "\n".join(kept + [mark]), True


def tail(text, budget, pointer):
    """暫存區在包裡只留**最後幾行** —— 新寫的那幾條在檔尾,而砍掉的部分要點名。

    與 `clip` 同一條規矩(砍了要出聲),差別只在留哪一頭:主檔從頭讀,暫存區是一行
    一條、越新越下面,從頭留會只留到最舊的那幾條。
    """
    raw = text.encode("utf-8")
    if len(raw) <= budget:
        return text, False
    mark = "…(只留最後幾行:全文見 %s)" % pointer
    room = budget - len(mark.encode("utf-8")) - 1
    kept = []
    used = 0
    for line in reversed(text.splitlines()):
        size = len(line.encode("utf-8")) + 1
        if used + size > room:
            break
        kept.append(line)
        used += size
    return "\n".join([mark] + list(reversed(kept))), True


def local_heading(rel, is_inbox, canon=False):
    """專案層那幾格的小標。**標題本身就寫出路徑**:預算緊的時候被最後一刀砍掉的是
    內容,而留下來的那一行要還說得出「這裡本來有一格、在哪個檔」。

    正本自己的暫存區(`canon`)不叫「本專案」:A 自己沒有專案層,那個字會讓讀的人
    以為疊了第二層規矩。"""
    if canon:
        return "### 還沒併進主檔的暫存 `%s`(最後幾行)" % rel
    return "### 本專案自己寫的 `%s`%s" % (rel, "(最後幾行)" if is_inbox else "")


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


def trim(render, parts, order, cut, limit):
    """最後一刀(#47):`render()` 組出來的整份超過 `limit` 就照 `order` 一格一格砍 ——
    每一格是 `(鍵, 名單上的名字, 指路, 砍法)`,砍到剛好放得下為止,每砍一格都進 `cut`
    (「砍過」名單;`render` 每次照它重組,名單變長吃掉的 bytes 也算進去)。

    以前這一刀是整份從尾巴砍:排在最後的暫存區內容沒了,名單卻只列分預算時砍過的
    那幾份 —— 「內容沒了、名單也沒列」與那一格從來沒有過長得一樣。
    """
    for key, label, pointer, shrink in order:
        before = len(render().encode("utf-8"))
        if before <= limit:
            break
        if not parts.get(key):
            continue
        old, added = parts[key], label not in cut
        if added:
            cut.append(label)
        over = len(render().encode("utf-8")) - limit
        parts[key], _ = shrink(old, max(len(old.encode("utf-8")) - over, 0), pointer)
        # 砍了反而更長(字比指路 + 名單上多一個名字還短)就放回去,換下一格。
        if len(render().encode("utf-8")) >= before:
            parts[key] = old
            if added:
                cut.pop()
    return render()


def pack(root, role, model, max_bytes, override=""):
    card_name, default_wanted, one_line = WANTED[role]
    wanted = wanted_sections(root, role, default_wanted)
    path = source_path(root, override)
    rel = os.path.relpath(path, root)
    found = blocks(path)
    # 名單上的字對到文件裡的哪一節:節號完全相等,或標題含那段字。對到的用文件裡的
    # 鍵,對不到的保留原字(底下會點名)。
    keys = [(num, resolve(found, num)) for num in wanted]
    card_rel = os.path.join(roles_dir(root), card_name)
    model_rel = model_card(root, model)

    # 專案層(D-021):`roles_dir` 不是 `memory/role` 的時候,這個 repo 在讀**別人的**
    # 正本規矩,而它自己的教訓寫在 `memory/`。兩層都疊;同一個目錄時只讀一次 ——
    # 疊兩次的話角色卡會整份出現兩遍,而讀的人會以為那是兩份不同的規矩。
    local = not is_canon(root)
    suffix = inbox_suffix(root)
    local_card_rel = os.path.join(local_dir("role"), card_name) if local else ""
    local_model_rel = (os.path.join(local_dir("model"), os.path.basename(model_rel))
                       if local and model_rel else "")
    local_card = read_text(os.path.join(root, local_card_rel)) if local_card_rel else ""
    local_memory = read_text(os.path.join(root, local_model_rel)) if local_model_rel else ""
    # 暫存區:主檔不在也要讀得到 —— 一條寫進 inbox 還沒併檔的教訓,與併過檔的那一條
    # 一樣會擋到人。主檔不在就**不出聲**(那不是壞掉,是還沒有人寫過)。
    # 來源**不綁 `local`**:暫存區永遠住在 `memory/`(這個 repo 自己寫的),而同步
    # 刻意不帶它(D-021)—— 綁在 `local` 上的話,正本自己的 inbox 兩邊都沒有讀者。
    inbox_mains = (os.path.join(local_dir("role"), card_name),
                   os.path.join(local_dir("model"), os.path.basename(model_rel))
                   if model_rel else "")
    inboxes = []
    for main_rel in inbox_mains:
        if not main_rel:
            continue
        one = main_rel[:-len(".md")] + suffix
        text = read_text(os.path.join(root, one))
        if text:
            inboxes.append((one, text))

    head = ["# 規則包:%s —— %s" % (role, one_line),
            EFFICIENCY_NOTE,
            "",
            "來源 `%s` @ %s,上限 %d bytes。**這是節錄,不是全文** —— "
            "每一段結尾寫了全文在哪。" % (rel, short_sha(root), max_bytes),
            "",
            "## 先讀這幾份(路徑,不是內容)",
            "- `%s` —— 你做什麼、不做什麼" % card_rel]
    if model_rel:
        head.append("- `%s` —— 你這個模型在這裡踩過什麼" % model_rel)
    head.append("- `%s` —— 共用規矩全文(下面只節錄 %d 節)" % (rel, len(wanted)))
    if local_card:
        head.append("- `%s` —— 本專案對這個角色的補充(底下疊進來了)" % local_card_rel)
    if local_memory:
        head.append("- `%s` —— 本專案對這個模型的補充(底下疊進來了)" % local_model_rel)
    for one, _text in inboxes:
        head.append("- `%s` —— %s還沒併進主檔的暫存(包裡只帶最後幾行)"
                    % (one, "本專案" if local else ""))
    if local:
        for one in project_notes(root):
            head.append("- `%s` —— 本專案的共用備忘(只給路徑,要看就 grep)" % one)
    head.append("")

    missing = [num for num, hit in keys if hit is None]
    if missing:
        # 名單與文件分岔了要**出聲**:一份靜靜少了兩節的規則包,與完整的那一份
        # 在畫面上長得一樣。
        head.append("> ⚠️ 共用規矩裡找不到這幾節:%s —— `rules.py` 的名單與 `%s` 分岔了。"
                    % (", ".join("§" + x for x in missing), rel))
        head.append("")

    card = read_text(os.path.join(root, card_rel))
    memory = read_text(os.path.join(root, model_rel)) if model_rel else ""
    fixed = "\n".join(head)

    # 預算要扣掉**鷹架自己**(小標、空行),不然三份加起來剛好等於上限時,
    # 組出來的整份必定超過 —— 而那會讓這一支自己在守自己時倒下。
    scaffold = ("## 角色卡(節錄)\n\n\n## 共用規矩節錄\n\n\n"
                "## 你這個模型的記憶(節錄)\n\n\n")
    local_rels = ([local_card_rel] if local_card else []) + \
                 ([local_model_rel] if local_memory else [])
    local_scaffold = "".join(local_heading(one, False) + "\n\n\n" for one in local_rels) \
        + "".join(local_heading(one, True, not local) + "\n\n\n" for one, _t in inboxes)
    # 節的下限(#47):每一節至少印「### 標題」+ 一行「全文在哪」,那一行本身就是一條
    # 提醒。它的 bytes 照節名算得出來,**先從預算扣掉**,角色卡 / 記憶 / 暫存區分的才是
    # 真的剩餘。以前是分完才用 `120 × 剩幾節` 擋:十節的下限(約 1.2 KB)撐破節錄那一份,
    # 超支由最後一刀從尾巴砍 —— 暫存區內容沒了、角色卡節錄 486→219 B(#37 量的)。
    picked = [hit for _num, hit in keys if hit is not None]
    floors = [len(("### %s\n%s\n" % (found[num][0],
                                     clip(found[num][1], 0, "`%s` §%s" % (rel, num))[0]))
                  .encode("utf-8")) + 1 for num in picked]
    free = (max_bytes - len(fixed.encode("utf-8")) - len(scaffold.encode("utf-8"))
            - len(local_scaffold.encode("utf-8")) - MEMORY_NOTE_BYTES
            - DELIVERY_NOTE_BYTES - 1 - sum(floors))

    def cut_note(cut):
        return ("> 這一份為了守住 %d bytes 砍過:%s —— 砍掉的部分去讀原檔。"
                % (max_bytes, "、".join(cut)))

    # 角色卡與模型記憶的各格:(字, 名單上的名字)。每一格至少會印一行「全文在哪」——
    # 與節的下限同一件事:前面的格分預算時,後面每一格的這一行先留著,不然最後一格的
    # 指路會撐破上限,由最後一刀替它砍別人。字比「指路 + 名單上多一個名字」還短的那一格
    # 砍了反而更長,整格照印。
    cells = {"card": (card, card_rel), "local_card": (local_card, local_card_rel),
             "mem": (memory, model_rel), "local_mem": (local_memory, local_model_rel)}
    least = {}
    for key, (text, one) in cells.items():
        mark = len(clip(text, 0, "`%s`" % one)[0].encode("utf-8"))
        size = len(text.encode("utf-8"))
        least[key] = size if size <= mark + len(("、" + one).encode("utf-8")) else mark

    def allocate(base):
        """把 `base` 分給暫存區、角色卡、節錄、模型記憶;回 (「砍過」名單, 各格的字)。"""
        # 暫存區(D-021)**非空的每一格至少留最後一行**(#47):照順序、放得下才給;
        # 再多的行只在 600 B 與剩餘的三分之一以內,前一格用剩的給下一格 —— 平分的話
        # 一行 250 B 的教訓在兩格都有字時永遠放不進去。一行都放不下的那一格只剩小標
        # (小標寫著檔名)並進「砍過」名單:**默默消失的一格與從來沒有過的一格長得一樣**。
        firsts = []
        for one, text in inboxes:
            mark = len(tail(text, 0, "`%s`" % one)[0].encode("utf-8"))
            first = min(len(text.encode("utf-8")),
                        mark + len(text.splitlines()[-1].encode("utf-8")) + 2)
            fits = sum(firsts) + first <= min(LOCAL_INBOX_BYTES, base)
            firsts.append(first if fits else 0)
        spare = max(min(LOCAL_INBOX_BYTES, max(base // 3, 0)) - sum(firsts), 0)
        parts, inbox_cut = {}, []
        for index, (one, text) in enumerate(inboxes):
            kept, was_cut = (tail(text, firsts[index] + spare // (len(inboxes) - index),
                                  "`%s`" % one) if firsts[index] else ("", True))
            spare -= max(len(kept.encode("utf-8")) - firsts[index], 0)
            if was_cut:
                inbox_cut.append(one)
            parts[one] = kept
        left = max(base - sum(len(kept.encode("utf-8")) for kept in parts.values()), 0)

        # 預算順序 = 重要性順序:角色卡(你是誰)> 共用規矩(你會被擋在哪)>
        # 模型記憶(你自己踩過什麼)。角色卡那 35% 在有專案主檔時拆成正本 20% / 專案 15%。
        # 比例是整個內容區(用剩的 + 節的下限)的比例 —— 節錄那一份本來就含它的下限;
        # 但每一格都從用剩的裡面拿,加起來不會超過它。
        cut = []
        area = left + sum(floors)

        def take(key, budget, later):
            text, one = cells[key]
            budget = max(min(budget, left - sum(least[k] for k in later)), least[key])
            parts[key], was_cut = clip(text, budget, "`%s`" % one) if text else ("", False)
            if was_cut:
                cut.append(one)
            return len(parts[key].encode("utf-8"))

        left -= take("card", int(area * (0.20 if local_card else 0.35)),
                     ("local_card", "mem", "local_mem"))
        left -= take("local_card", int(area * 0.15), ("mem", "local_mem"))

        # 節的預算**逐節分,而且照名單的順序先給滿** —— 一整包分的話,第一節(最長的
        # 那一節)會把額度吃光,後面九節連標題都不會出現,讀的人因此不知道還有那九條規矩。
        # 後面每一節的下限先留著(`floors`);+2 是這一節結尾的換行與節間空行。
        budget = sum(floors) + min(max(int(area * 0.45) - sum(floors), 0),
                                   max(left - least["mem"] - least["local_mem"], 0))
        body, rules_cut, used = [], False, 0
        for index, num in enumerate(picked):
            title, text = found[num]
            line = "### %s" % title
            cost = len(line.encode("utf-8")) + 1
            allow = max(budget - used - cost - 2 - sum(floors[index + 1:]), 0)
            one, was_cut = clip(text, allow, "`%s` §%s" % (rel, num))
            rules_cut = rules_cut or was_cut
            chunk = "%s\n%s\n" % (line, one)
            used += len(chunk.encode("utf-8")) + 1
            body.append(chunk)
        parts["rules"] = "\n".join(body).strip("\n")
        if rules_cut:
            cut.append("%s 的節錄" % rel)
        left -= used - sum(floors)

        # 模型記憶:A 自己拿「角色卡與節錄用剩的」。兩層的時候給固定比例(正本 12% /
        # 專案 8%)—— 20 B 的專案補充不該因為排在後面就被一句比它還長的「截斷」取代。
        left -= take("mem", int(area * (0.12 if local_memory else 0.20)) if local else left,
                     ("local_mem",))
        take("local_mem", int(area * 0.08), ())
        return cut + inbox_cut, parts

    # 「砍過」那一句多長要分完才知道:先當它不在,分完量它的真長度再重分,留下放得下的
    # 那幾輪裡留得最少的一輪(名單幾輪就不再變)。以前固定留 260 B —— 兩層疊起來時多留
    # 的那幾十 B,正好是模型記憶放不放得下的差別。
    reserve, fits = 0, []
    for _round in range(6):
        cut, parts = allocate(free - reserve)
        need = len(cut_note(cut).encode("utf-8")) + 2 if cut else 0
        if need <= reserve:
            fits.append((reserve, cut, parts))
            if need == reserve:
                break
        reserve = need
    if fits:
        _reserve, cut, parts = min(fits, key=lambda one: one[0])

    def render():
        out = list(head)
        if cut:
            # **砍過這句話放在最前面**:放在最後的話,它自己會被最後那一刀砍掉,
            # 而一份砍過卻沒說砍過的規則包,與完整的那一份長得一樣。
            out += [cut_note(cut), ""]
        out += ["## 角色卡(節錄)", parts["card"] or "(找不到 %s)" % card_rel, ""]
        if parts["local_card"]:
            out += [local_heading(local_card_rel, False), parts["local_card"], ""]
        for one, _text in inboxes:
            if one.startswith(local_dir("role")):
                out += [local_heading(one, True, not local), parts[one], ""]
        out += ["## 共用規矩節錄", parts["rules"] or "(一節都沒抽到)", ""]
        if memory or parts["local_mem"]:
            out += ["## 你這個模型的記憶(節錄)"]
            out += [parts["mem"], ""] if memory else []
            if parts["local_mem"]:
                out += [local_heading(local_model_rel, False), parts["local_mem"], ""]
        for one, _text in inboxes:
            if not one.startswith(local_dir("role")):
                out += [local_heading(one, True, not local), parts[one], ""]
        return "\n".join(out)

    # 最後一道:**組出來的整份**再量一次。上面的分配照真的長度算,還超過的那一次(例如
    # 節的下限本身就塞不下)要在這裡被擋住,不是在呼叫者那裡變成一份超過上限的派工文。
    # 記憶回寫段與結構化交付段在這一刀**之外**:先量給它們,砍的是前面那幾份 ——
    # 它們是固定文字,砍到一半的「只在三種時刻寫」會變成「隨時可以寫」,砍到一半的
    # 「照實留空不要編」會變成「留空」。
    limit = max_bytes - MEMORY_NOTE_BYTES - DELIVERY_NOTE_BYTES - 4
    order = ([("mem", model_rel, "`%s`" % model_rel, clip),
              ("local_mem", local_model_rel, "`%s`" % local_model_rel, clip),
              ("rules", "%s 的節錄" % rel, "`%s`" % rel, clip),
              ("card", card_rel, "`%s`" % card_rel, clip),
              ("local_card", local_card_rel, "`%s`" % local_card_rel, clip)]
             + [(one, one, "`%s`" % one, tail) for one, _text in inboxes])
    text = trim(render, parts, order, cut, limit)
    # 照順序砍完還放不下(上限小到連前言都塞不下)才整份從尾巴砍。這一刀的指路**列出
    # 砍過的那幾份**:連上面那句「砍過」都留不住時,這個記號是最後一個還說得出「哪一格
    # 不見了」的地方。
    text, _ = clip(text, limit, "、".join(cut) if cut else "`%s` 與上面列的那幾份" % rel)
    # 記憶回寫**留在最後一行**:它是收工前最後一個動作,而讀的人從尾巴往回讀。
    return (text.rstrip("\n") + "\n\n" + DELIVERY_NOTE + "\n\n" + MEMORY_NOTE)


def cmd_pack(args):
    root = event.repo_root()
    role = ALIASES.get(args.role.lower())
    if not role:
        sys.stderr.write("rules: 不認得角色 %r —— 認得的是 %s\n"
                         % (args.role, " / ".join(sorted(set(ALIASES)))))
        return 2
    text = pack(root, role, args.model, args.max_bytes, args.source)
    size = len(text.encode("utf-8"))
    if size > args.max_bytes:
        # 守衛自己要守得住:超過上限就是這一支壞了,不是「差一點」。
        sys.stderr.write("rules: 壓不到 %d bytes(現在 %d)—— 這一支自己壞了\n"
                         % (args.max_bytes, size))
        return 1
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    if args.stats:
        sys.stderr.write("rules: %s %d bytes(上限 %d;不追求快 %d bytes;"
                         "結構化交付 %d bytes;記憶回寫 %d bytes)\n"
                         % (role, size, args.max_bytes, EFFICIENCY_NOTE_BYTES,
                            DELIVERY_NOTE_BYTES, MEMORY_NOTE_BYTES))
    return 0


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

    pack_cmd = subs.add_parser("pack")
    pack_cmd.add_argument("role")
    pack_cmd.add_argument("--model", default="")
    pack_cmd.add_argument("--max-bytes", type=int, default=DEFAULT_MAX)
    pack_cmd.add_argument("--source", default="")
    pack_cmd.add_argument("--stats", action="store_true")
    pack_cmd.set_defaults(run=cmd_pack)

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
