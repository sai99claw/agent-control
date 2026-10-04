# 角色卡整理處置總表(A,#78,2026-10-04,fable 單模型 D-043)

三張卡(opener / implementer / verifier)與它們的 inbox,照 D-013 3 整理:卡上只留原則、行為準則、思考方式;
案例只留票號;指令、旗標、路徑、行號、commit 指紋移到 `docs/reference/role-procedures.md`(各角色一節,節名含「角色操作細節」)。
逐行處置(含理由)在 `discussions/2026-10-04-memory-remediation-<卡>-fable.md`;這一份只列「原句 → 處置 → 去處」。
cap_chars / cap_history 一字不動;不跑 `memory.py consolidate`(它要兩個模型的討論檔,D-043 改單模型)。

## 數字(實測 2026-10-04)

| 卡 | 正文字元 改前 → 改後(上限) | 行數 | lint 改前 → 改後 | inbox 行 改前 → 改後 | 留給 #88 |
|---|---|---|---|---|---|
| opener | 2449 → 1776(2000;票要 ≤ 1800) | 17 → 13 | 16 → 0 | 7 → 0 | 224(P13 要 ≥ 200) |
| implementer | 2700 → 2307(2700;票要 ≤ 2550) | 19 → 18 | 30 → 0 | 14 → 0 | 393(P14 要 ≥ 150) |
| verifier | 1788 → 1529(2000;票要 ≤ 1850) | 17 → 13 | 14 → 0 | 4 → 0 | 471(P15 要 ≥ 150) |

memory-allow 例外:0 條。

## opener.md

| 原句(改前行) | 處置 | 去處 |
|---|---|---|
| L3 做:…在哪一層驗(單元、API、瀏覽器)…跑競爭迴圈、算色組… | 保留(縮) | 卡「做」 |
| L4 不做:…不執行整支腳本(fullsuite/land)… | 保留(縮) | 卡「不做」;併入 L5 的原則句 |
| L5 開完票只准 land.sh docs、只准自己那張票檔 —— `sh scripts/land.sh docs "tickets: #<n> 開票" tickets/<n>.json` | 原則留卡、指令出卡 | 卡「不做」末句(D-025 ③);reference 開題者 /「票的欄位契約與派工範本」 |
| L6 票 JSON(欄位契約 `tickets/SCHEMA.md`,派工範本 `templates/dispatch-opener.md`)+ `ticket.py create --outline` | 原則留卡、路徑與指令出卡 | 卡「交付物」;reference 開題者 /「票的欄位契約與派工範本」 |
| L7 摘要寫進票,不要只回在對話裡(#29 A2) | 保留(併) | 卡「交付物」末 |
| L8 信誰 / L9 上限 / L10 第二輪假設 | 保留 | 卡同名段 |
| L11 `verify_strings` 是 list[str] 內容字串(落地對整個分支 `git grep -F`,不是檔名、不是 {path,contains}) | 改寫(B4) | 卡「票面四格」:「閘門比的是改動內文(diff 的增刪行與新檔全文),diff 檔頭與檔名不算,新檔就挑檔內一定會有的字,如類名、函式名」;原說法與 `--verify-string` 認法進 reference 開題者 /「票面四格自檢」 |
| L11 `tags` 只能用 `verify/TAGS.md` 登記過的;指令先 `--help` | 保留(去路徑) | 卡「票面四格」;路徑進 reference 同節 |
| L11 自檢:`python3 -c 'import json;…print("ok")'` | 移至 reference | reference 開題者 /「票面四格自檢」 |
| L12 needs_verifier=true 再判 interface_fixed(D-H38 ②,#60) | 保留(併) | 卡「needs_verifier 怎麼判」 |
| L13 開票前實測票面假設(D-033;三張產品票第 1 輪全 ticket-wrong) | 保留(刪案例描述) | 卡「開票前實測票面假設」 |
| L14–L17 三條開題規則 (a)(b)(c)(含反例 #658–#664) | 保留(縮;票號留) | 卡「寫入範圍與驗收的三條開題規則」;反例清單另抄 reference 開題者 /「寫入範圍」 |
| inbox L1 每張票預設要驗證者案例;不需要的只能是寫明理由的那種(#8) | 升格 | 卡「needs_verifier 怎麼判」 |
| inbox L2 不追求快,只看效率(D-016) | 重複 | 規則包前言每份都印 D-016 |
| inbox L3 設計為未來 token 打算(D-018) | 升格 | 卡「取捨看之後的 token」 |
| inbox L4 跨 repo 依賴不能寫 depends_on;sync-to-project 名單只十支…cmp -s(#648) | 一半升格、一半移至 reference | 卡「取捨看之後的 token」末句;reference 開題者 /「跨 repo」 |
| inbox L5 allowed_write_paths 要含驗證者會建立的案例檔(#23) | 升格 | 卡三條規則 (a) |
| inbox L6 AC 的驗證者案例住 tests/ 時 verify.tags 要留空(#29) | 移至 reference | reference 開題者 /「needs_verifier 怎麼判」 |
| inbox L7 needs_verifier 用 §5.8 第 3 問判(#68) | 升格 | 卡「needs_verifier 怎麼判」 |

## implementer.md(L1–L5 front matter 一字不動)

| 原句(改前行) | 處置 | 去處 |
|---|---|---|
| L6 標題尾 `(84864f3,2026-09-13,實測)` 與各段的 84864f3 / b65ee79 / 82a8b1d / db32c07 | 指紋出卡 | reference 實作者 /「歷史來源」 |
| L8 跑票閘門對照表挑到的那一組(副本裡沒有 `.git`,`--branch` 是空閘門,要用檔名形式叫) | 改寫(B3(a) 一半) | 卡「做」:「只跑派工文『只准跑的測試』段列的測試」+「分支形式的閘門是空閘門(用檔名形式叫)」;旗標進 reference 實作者 /「票閘門的叫法」 |
| L10 不碰專案 `CLAUDE.md` 列的受保護埠;不寫 `verify/**` | 保留(去路徑) | 卡「不做」 |
| L11 `patch.diff`…`EVIDENCE.md`;檔頭只准 `base/…` / `work/…` | 保留(去檔名) | 卡「交付物」;形狀細節進 reference 實作者 /「交付物的形狀」 |
| L12–L13 EVIDENCE 五段、result 塊(D-017,#20 ⑦) | 保留(併一段) | 卡「EVIDENCE 必備五段」 |
| L14 交付前必跑 `scripts/verify.py --tag <…>`、整支瀏覽器模組;時序類的票負載下(`nice -n 19` + 4 支 `yes`)跑 3 次;三組 Ran/OK 貼進 EVIDENCE;一輪把同一層的 bug 全露出來 | 改寫(B3(a)) | 卡「回歸不是你的事」:只跑派工文「只准跑的測試」段列的測試;回歸是閘門的事,全套只在落地跑一次;負載下重跑只在票的 test_plan 列了才做(#87 A18)。配方進 reference 實作者 /「票閘門的叫法」 |
| L15 只擷取 `^Ran \|^OK\|^FAILED\|^(FAIL\|ERROR):`;`claude -p` 不會再醒(#26) | 保留(白話) | 卡「不准輪詢」;regex 進 reference 實作者 /「票閘門的叫法」 |
| L16 先讀 `reports/t<票號>/<run_id>/status.json` | 保留(白話「那一輪的狀態檔」) | 卡「紅了誰修」;路徑進 reference 實作者 /「交付物的形狀」 |
| L17 停,寫成票的 `objections[]` 一筆(…)再報主線 | 改寫(B3(b)) | 卡「票寫錯 / 需要裁示 / 案例本身錯」:寫一筆反駁後交件停下,不報主線 —— 反駁由自動派工起開題者判、判完接回(D-041,#89);test_defect 半句與 D-018 不繞路保留;欄位名進 reference 實作者 /「反駁的形狀」 |
| L18 `env \| grep ^AC_`、`env -u AC_ROOT -u AC_TICKET`、`git ls-files`、`cp -R work` + `git init`、`PYTHONDONTWRITEBYTECODE=1`、`diff -rq base work \| grep '^Only in work'`、`*.env-suspect.json`、交完刪 work/ 與 base/ | 原則留卡、指令出卡 | 卡「副本環境」(原則句 + 指路);reference 實作者 /「副本環境」 |
| L19 `diff(舊 work, 新 work)` 反向核對 | 保留(白話) | 卡「測試與 patch 的寫法」;細節進 reference 實作者 /「測試與 patch 的寫法(細節)」 |
| inbox L1 四問(#68) | 升格 | 卡「EVIDENCE 必備五段」③ |
| inbox L2 副本跑會發事件的腳本新建 board/events.jsonl(#53) | 升格 + reference | 卡「副本環境」;reference 實作者 /「副本環境」 |
| inbox L3 OBJECTION 行不准寫絕對家目錄路徑或專案名(#53) | 升格 | 卡「票寫錯…」 |
| inbox L4 `$var` 緊接全形字(#57) | 升格(白話) | 卡「測試與 patch 的寫法」;`${var}` 進 reference |
| inbox L5 副本跑 scripts/* 前先 env -u(#59) | 升格(原則) | 卡「副本環境」;指令進 reference |
| inbox L6 真實專案根跑 new-session.sh 前先複製 board 資料(#62) | 移至 reference | reference 實作者 /「副本環境」 |
| inbox L7 全套超過前景 600s 分批;DEFAULT_CONFIG 加預設值前 grep(#70) | 升格(兩半) | 卡「不准輪詢」+「不做」;數字進 reference |
| inbox L8 寫票指令前 AC_ROOT=$PWD(#71) | 升格 | 卡「副本環境」(與 L5 合句) |
| inbox L9 子行程傳 env AC_ROOT=ROOT(#71) | 升格 | 卡「副本環境」 |
| inbox L10 恆真斷言改寫在 base 舊案例上也跑(#72) | 升格 | 卡「測試與 patch 的寫法」 |
| inbox L11 同一輪被重派、副本已重展(#72) | 升格 | 卡「紅了誰修」 |
| inbox L12 合併案例的 docstring 變異在 base 重跑(#73) | 升格 | 卡「測試與 patch 的寫法」 |
| inbox L13 control_harness 沙盒 memory.applies_to(#76) | 移至 reference | reference 實作者 /「副本環境」 |
| inbox L14 副本沒有 .git,test_no_project_names 掃到 0 個檔(#89) | 升格(原則) | 卡「做」;測試名進 reference |

## verifier.md

| 原句(改前行) | 處置 | 去處 |
|---|---|---|
| L1 2026-09-21 D-G122 改寫 | 保留(補「依來源專案」) | 卡標題 |
| L4 `verify/<feature>/test_ticket_<n>.py`、`verify/TAGS.d/<票號>.md` | 保留(白話) | 卡「做」①;路徑進 reference 驗證者 /「案例與交付物」 |
| L5 `python3 scripts/verify-case.py red <票號> --candidate <$W/work>`、`baseline-red.json`、`verify-case.py check`、`ticket.py close`、`verify-case.py extract`、`EVIDENCE-verifier.md` | 原則留卡、指令出卡 | 卡「做」②(驗紅工具 / 對勾工具 / 套 patch 的腳本);reference 驗證者 /「案例與交付物」 |
| L5 主線收件時併進票 | 改寫(D-041) | 卡「做」②:「套 patch 的腳本收件時併進票」 |
| L6–L8 主線套 patch 時走 `sh scripts/apply.sh … --evidence-verifier …`,抽成 `reports/t<票號>/<run_id>/result-verifier-round<輪>.json` | 原則留卡、指令與路徑出卡 | 卡「它有收件者」;reference 驗證者 /「案例與交付物」 |
| L10 由 auto-fix.sh 第 1 輪自動派…等 worker 交出 patch-round1…apply.sh 併進票 | 保留(白話) | 卡「誰派」;reference 驗證者 /「誰派、誰跑」 |
| L11 反例 T #681:等 `from === 當月-01` | 保留(白話) | 卡「四問」;原字樣進 reference 驗證者 /「誰派、誰跑」 |
| L13 只跑自己的案例與 `verify-case.py check` | 保留(白話) | 卡「不跑 tag 回歸那一整組」 |
| L14 紅了走 WORKFLOW 的自動派工,不回到驗證者 | 改寫(B3(c)) | 卡「誰判對錯」:閘門紅由自動派工下一輪處理,不回到你;案例的 oracle / fixture 錯了(test_defect)則由你修 —— 實作者不准放寬斷言(D-041) |
| L16 案例寫不出來就退回開題者,不猜 | 改寫(B3(c)) | 卡「上限」:就提反駁、由開題者判,不猜(D-041) |
| inbox L1 案例檔由你獨占(#630) | 升格 | 卡「patch 與案例檔的歸屬」 |
| inbox L2 不追求快,只看效率(D-016) | 重複 | 規則包前言 |
| inbox L3 設計為未來 token 打算(D-018) | 歸檔 | 驗證者不做設計取捨;原文在 DECISIONS、opener 卡、模型卡 |
| inbox L4 patch.diff 在 $W 裡跑 diff -ruN base work,檔頭只准 base/… work/…(#25 #26 #28) | 升格(原則)+ reference(細節) | 卡「patch 與案例檔的歸屬」;reference 驗證者 /「案例與交付物」 |

## 沒改的

- 三張卡的 cap_chars / cap_history(implementer 的 front matter 五行一字不動;opener / verifier 沒有 front matter,上限來自設定)。
- 不寫 #88 P13「判反駁」、P14「被接回時從原副本續做」、P15「被接回時只改受票面差異影響的案例」。
- 不碰 memory/role/README.md、模型卡、專案卡、其他角色卡、scripts/、tests/、board/、templates/。
