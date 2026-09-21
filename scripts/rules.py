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
}
ALIASES = {"implementer": "worker", "impl": "worker", "verify": "verifier",
           "verifier": "verifier", "opener": "opener", "open": "opener",
           "main": "main", "worker": "worker"}


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


def read_text(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def pack(root, role, model, max_bytes, override=""):
    card_name, wanted, one_line = WANTED[role]
    path = source_path(root, override)
    rel = os.path.relpath(path, root)
    found = blocks(path)
    card_rel = os.path.join("memory", "role", card_name)
    model_rel = os.path.join("memory", "model", "%s.md" % model) if model else ""

    head = ["# 規則包:%s —— %s" % (role, one_line),
            "",
            "來源 `%s` @ %s,上限 %d bytes。**這是節錄,不是全文** —— "
            "每一段結尾寫了全文在哪。" % (rel, short_sha(root), max_bytes),
            "",
            "## 先讀這幾份(路徑,不是內容)",
            "- `%s` —— 你做什麼、不做什麼" % card_rel]
    if model_rel:
        head.append("- `%s` —— 你這個模型在這裡踩過什麼" % model_rel)
    head.append("- `%s` —— 共用規矩全文(下面只節錄 %d 節)" % (rel, len(wanted)))
    head.append("")

    missing = [num for num in wanted if num not in found]
    if missing:
        # 名單與文件分岔了要**出聲**:一份靜靜少了兩節的規則包,與完整的那一份
        # 在畫面上長得一樣。
        head.append("> ⚠️ 共用規矩裡找不到這幾節:%s —— `rules.py` 的名單與 `%s` 分岔了。"
                    % (", ".join("§" + x for x in missing), rel))
        head.append("")

    card = read_text(os.path.join(root, card_rel))
    memory = read_text(os.path.join(root, model_rel)) if model_rel else ""
    fixed = "\n".join(head)

    # 預算要扣掉**鷹架自己**(小標、空行、結尾那一句),不然三份加起來剛好等於上限時,
    # 組出來的整份必定超過 —— 而那會讓這一支自己在守自己時倒下。
    scaffold = ("## 角色卡(節錄)\n\n\n## 共用規矩節錄\n\n\n"
                "## 你這個模型的記憶(節錄)\n\n\n")
    room = max(max_bytes - len(fixed.encode("utf-8"))
               - len(scaffold.encode("utf-8")) - 260, 0)
    cut = []
    # 預算順序 = 重要性順序:角色卡(你是誰)> 共用規矩(你會被擋在哪)>
    # 模型記憶(你自己踩過什麼)。
    card_text, card_cut = clip(card, int(room * 0.35), "`%s`" % card_rel)
    if card_cut:
        cut.append(card_rel)

    # 節的預算**逐節分,而且照名單的順序先給滿** —— 一整包分的話,第一節(最長的
    # 那一節)會把額度吃光,後面九節連標題都不會出現,讀的人因此不知道還有那九條規矩。
    # 每一節至少留標題 + 一句「全文在哪」:那一行本身就是一條提醒。
    picked = [num for num in wanted if num in found]
    budget = int(room * 0.45)
    reserve = 120
    body, rules_cut, used = [], False, 0
    for index, num in enumerate(picked):
        title, text = found[num]
        line = "### %s" % title
        cost = len(line.encode("utf-8")) + 1
        left = len(picked) - index - 1
        allow = max(budget - used - cost - left * reserve, 0)
        one, was_cut = clip(text, allow, "`%s` §%s" % (rel, num))
        rules_cut = rules_cut or was_cut
        chunk = "%s\n%s\n" % (line, one)
        used += len(chunk.encode("utf-8"))
        body.append(chunk)
    rules_text = "\n".join(body).strip("\n")
    if rules_cut:
        cut.append("%s 的節錄" % rel)

    mem_text, mem_cut = (clip(memory, max(room - int(room * 0.35) - used, 0),
                              "`%s`" % model_rel) if memory else ("", False))
    if mem_cut:
        cut.append(model_rel)

    out = list(head)
    if cut:
        # **砍過這句話放在最前面**:放在最後的話,它自己會被最後那一刀砍掉,
        # 而一份砍過卻沒說砍過的規則包,與完整的那一份長得一樣。
        out += ["> 這一份為了守住 %d bytes 砍過:%s —— 砍掉的部分去讀原檔。"
                % (max_bytes, "、".join(cut)), ""]
    out += ["## 角色卡(節錄)", card_text or "(找不到 %s)" % card_rel, "",
            "## 共用規矩節錄", rules_text or "(一節都沒抽到)", ""]
    if memory:
        out += ["## 你這個模型的記憶(節錄)", mem_text, ""]
    text = "\n".join(out)
    # 最後一道:**組出來的整份**再量一次。上面的分配是估的,而估錯的那一次要在這裡
    # 被擋住,不是在呼叫者那裡變成一份超過上限的派工文。
    text, _ = clip(text, max_bytes, "`%s` 與上面列的那幾份" % rel)
    return text


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
        sys.stderr.write("rules: %s %d bytes(上限 %d)\n" % (role, size, args.max_bytes))
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
        sys.stdout.write("            角色卡 memory/role/%s;節 %s\n"
                         % (card, "、".join("§" + x for x in wanted)))
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
