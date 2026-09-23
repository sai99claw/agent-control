"""掃全 repo:不准出現上一個專案的名字,也不准出現這台機器的絕對路徑。

## 為什麼這一條要是一支測試
這個 repo 是從一個真實專案抽出來的,而抽得乾不乾淨**沒有辦法用眼睛判斷** ——
一個漏抽的專案名、一條寫死的家目錄路徑,在別人 clone 下來之前都看不出問題,而那時
它是「這份工具只在作者的機器上跑得起來」。

## 守衛自己不准寫死那幾個字
禁詞在這支檔案裡是**拆開拼的**。不然這支守衛自己就會是它抓到的第一個檔,而處置只會是
在守衛裡加一格例外 —— 加白名單只是把這次的判斷藏起來(`docs/DISPATCH-TEMPLATE.md`
§5.7)。**這條連家目錄的絕對路徑前綴也算**:任何要在這支檔案裡提到那個前綴的地方,
都得拼 `ABSOLUTE_HOME` 這個常數,不准照抄字面(#30 round 2 撞過)。

## 兩級嚴格,而不是一張要靠人維護的白名單
- **文件級** = **任何目錄下的 `.md`**(`docs/TODO.md` §0 整節就是從那個專案搬過來的
  遷移計畫,不指名就寫不出來;`README.md`、`memory/role/*.md` 這種不在 `docs/` 底下
  的 `.md` 一樣算 —— 判準是副檔名,不是目錄),再加上 **`docs/**.html`**(
  `docs/FLOW.html` 畫的是這條管線自己的請求流程,箭頭上標著「這一步是從哪個專案的
  教訓來的」也是同一種內容)與 **`tickets/*.json`**(票的紀錄,一張票的
  outline/test_plan 裡引用「上一個專案怎麼做」是在記錄事實,不是替這個 repo 寫死
  依賴)。它們只被禁一件事:**絕對路徑**——文件可以指名來源專案,但不能替換成
  「只有作者的機器跑得起來」。
- **其他所有檔**(程式、設定、測試、`docs/` 之外的 `.html`、`tickets/` 之外的
  `.json`)四個都禁。專案特有的東西走 `board/config.json` 或專案自己的
  `scripts/gate.sh`。

兩級是一條**規則**,不是一張名單 —— 名單要靠人記得更新,而它靜默失效的那天沒有人會
知道;規則是「`.md` 這個副檔名」+「`docs/` 前綴 + `.html`」+「`tickets/` 前綴 +
`.json`」三條判準的聯集,一樣不用人維護清單。
"""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import ROOT  # noqa: E402

# 拆開拼:這支檔案自己不准是它抓到的第一個。
PRIOR_PROJECT = "tab" + "by"
PRIOR_PRODUCT = "our" + "pot"
MACHINE_USER = "frank" + "sai"
ABSOLUTE_HOME = "/Us" + "ers/"

EVERYWHERE = (ABSOLUTE_HOME,)
CODE_ONLY = (PRIOR_PROJECT, PRIOR_PRODUCT, MACHINE_USER)
# 文件級的判準:任何 `.md`(不分目錄)+「`docs/` 前綴 + `.html`」+
# 「`tickets/` 前綴 + `.json`」——見上面「兩級嚴格」。
DOCUMENT_SUFFIXES = (".md",)
DOCUMENT_DIR_SUFFIXES = (("docs/", ".html"),)
DOCUMENT_DIR_ONLY = ("tickets/",)
DOCUMENT_ONLY_SUFFIX = ".json"
BINARY_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf",
                   ".woff", ".woff2", ".zip", ".gz")


def is_document(path):
    """任何 `.md`、`docs/**.html`、`tickets/*.json` 才是文件級,其餘一律程式級。"""
    lower = path.lower()
    if lower.endswith(DOCUMENT_SUFFIXES):
        return True
    if any(lower.startswith(prefix) and lower.endswith(suffix)
           for prefix, suffix in DOCUMENT_DIR_SUFFIXES):
        return True
    return (lower.startswith(DOCUMENT_DIR_ONLY)
            and lower.endswith(DOCUMENT_ONLY_SUFFIX))


def repo_files(where=ROOT):
    """追蹤中的 + 沒被忽略的未追蹤檔。

    `-z` 不是效能參數,是正確性參數:不加它,非 ASCII 路徑會被逃逸印出來,而逃逸過
    的字串 `open()` 開不起來 —— **那會安靜地變成「掃不到」**。
    """
    names = []
    for args in (["ls-files", "-z"], ["ls-files", "-z", "--others", "--exclude-standard"]):
        done = subprocess.run(["git", "-C", where, *args],
                              capture_output=True, text=True, timeout=60)
        names.extend(name for name in done.stdout.split("\0") if name)
    return sorted(set(names))


def offences(path, text):
    """回傳這個檔犯了哪幾條。**用判準本身去數**,不用近似的條件。"""
    banned = EVERYWHERE if is_document(path) else EVERYWHERE + CODE_ONLY
    return [word for word in banned if word.lower() in text.lower()]


class NoProjectNames(unittest.TestCase):

    def read(self, name):
        with open(os.path.join(ROOT, name), encoding="utf-8", errors="replace") as handle:
            return handle.read()

    def test_the_scan_sees_something_at_all(self):
        """**一個掃了零個檔的守衛是綠的。** 先釘住它真的看到了東西
        (`docs/DISPATCH-TEMPLATE.md` §5.5「沒有輸入也算跑過」)。"""
        names = repo_files()
        self.assertGreater(len(names), 20, "掃到的檔太少,八成是 git 那一問失敗了")
        for must in ("scripts/land.sh", "board/board.py", "CLAUDE.md"):
            self.assertIn(must, names)

    def test_no_file_carries_an_absolute_home_path(self):
        bad = []
        for name in repo_files():
            if name.lower().endswith(BINARY_SUFFIXES):
                continue
            try:
                text = self.read(name)
            except (OSError, UnicodeError):
                continue
            if ABSOLUTE_HOME.lower() in text.lower():
                bad.append(name)
        self.assertEqual(bad, [], "這幾個檔寫死了這台機器的絕對路徑")

    def test_no_code_or_config_names_the_project_it_came_from(self):
        bad = {}
        for name in repo_files():
            if name.lower().endswith(BINARY_SUFFIXES):
                continue
            try:
                text = self.read(name)
            except (OSError, UnicodeError):
                continue
            found = offences(name, text)
            if found:
                bad[name] = found
        self.assertEqual(bad, {},
                         "專案特有的東西要走 board/config.json 或專案自己的 gate.sh")

    def test_the_guard_really_catches_a_planted_one(self):
        """**沒驗紅的守衛等於沒有守衛。** 這一條把每一個禁詞各種進去一次,確認掃得到
        —— 沒有它,上面兩條在「禁詞表是空的」時也會是綠的。"""
        for word in EVERYWHERE + CODE_ONLY:
            self.assertEqual(offences("scripts/x.py", "前面 %s 後面" % word), [word])

    def test_the_level_comes_from_the_suffix_not_from_the_file_name(self):
        """同一段字,放在 `.md` 裡放行(不分目錄)、放在別的副檔名裡照抓 —— 那是一條
        **規則**,不是一張要靠人記得更新的名單。"""
        text = "從 %s 搬過來" % PRIOR_PROJECT
        self.assertEqual(offences("docs/TODO.md", text), [])
        self.assertEqual(offences("README.md", text), [],
                         "根目錄的 .md 一樣是文件級,不是只有 docs/ 底下那些")
        self.assertEqual(offences("memory/role/README.md", text), [],
                         ".md 的文件級判準看副檔名,不看是不是在 docs/ 底下")
        self.assertEqual(offences("scripts/land.sh", text), [PRIOR_PROJECT])
        # 絕對路徑兩級都抓。
        self.assertEqual(offences("docs/TODO.md", "在 %s 底下" % ABSOLUTE_HOME),
                         [ABSOLUTE_HOME])

    def test_docs_html_and_ticket_json_are_document_level(self):
        """#30:`docs/FLOW.html`(.html 文件)與票檔紀錄(`tickets/*.json`)是文件級
        —— 可以指名來源專案,但絕對路徑仍全面禁止(acceptance #2)。"""
        text = "這一步是從 %s 學到的" % PRIOR_PROJECT
        self.assertEqual(offences("docs/FLOW.html", text), [])
        self.assertEqual(offences("tickets/23.json", text), [])
        # .html 只有在 docs/ 底下才算文件級,別的目錄照舊禁。
        self.assertEqual(offences("templates/x.html", text), [PRIOR_PROJECT])
        home_text = "在 %s底下跑" % ABSOLUTE_HOME
        self.assertEqual(offences("docs/FLOW.html", home_text), [ABSOLUTE_HOME])
        self.assertEqual(offences("tickets/23.json", home_text), [ABSOLUTE_HOME])

    def test_non_docs_non_markdown_files_are_not_relaxed(self):
        """#30 acceptance #3:守衛沒被放寬到程式與設定 —— `scripts/*.py`、
        `board/config.json` 含來源專案名仍紅。(`.md` 本身一律文件級,見上面
        `test_the_level_comes_from_the_suffix_not_from_the_file_name`;這裡驗的是
        非 `.md`、非 `docs/**.html`、非 `tickets/*.json` 的東西沒被放寬。)"""
        text = "從 %s 搬過來" % PRIOR_PROJECT
        self.assertEqual(offences("scripts/x.py", text), [PRIOR_PROJECT])
        self.assertEqual(offences("board/config.json", text), [PRIOR_PROJECT])

    def test_real_flow_html_passes_the_project_name_check(self):
        """對真正的 `docs/FLOW.html` 內容重現 #30 的 acceptance #1 —— 這份檔在修好
        之前會被 `test_no_code_or_config_names_the_project_it_came_from` 印出來
        (該測試靠 `git ls-files` 找檔案,在沒有 `.git` 的派工副本裡掃不到任何檔,
        所以這裡改成直接讀檔驗證同一個判準,不依賴 git)。`tickets/23.json` 的內容
        由主線另外處理(絕對路徑那一格),這裡不對它的即時內容斷言,避免跟主線的
        修改撞期。"""
        flow_html = self.read("docs/FLOW.html")
        self.assertEqual(offences("docs/FLOW.html", flow_html), [],
                         "docs/FLOW.html 提到來源專案名或絕對路徑,修好後這裡不該再列")


if __name__ == "__main__":
    unittest.main()
