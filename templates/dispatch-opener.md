# 派工文範本 —— 開題者(D-015;#29 A2,2026-09-23)

**怎麼用**:整份複製,把 `<…>` 換掉。這一份**不重貼規則**(規則在角色卡;共用規矩
`docs/DISPATCH-TEMPLATE.md` 按角色卡指的節查,不整份讀),只指路 + 這張票獨有的那幾件事。
前言請用 `python3 scripts/rules.py pack opener --model <模型>` 產(**不要整份貼**共用規矩);
它非零就**不派**,照 stderr 補缺項,不要手寫前言代替(#74)。
**模型不在這裡選**(#122,D-049 ②):`<模型>` 是 `python3 scripts/role-models.py resolve --role opener [--complexity complex] --items <N>` 印的那個
(一般開題 opus;明確複雜 astra → fable)。主線獨立派工用 `sh scripts/new-session.sh dispatch opener --items <N> --prompt <這份填好的派工文>
[--complexity complex]`:`<N>` = 這次要交的票數,超過候選 `dispatch_cap`(astra 5)就 rc=2、印切批建議、不起程序 —— 切成小批再派。

> ⚠️ 開題者**不改產品碼與 repo 檔**(票檔、triage 檔、暫存目錄裡的實測可以;唯讀指令與 `--help` 可以跑)、
> **不 git 寫入**(只有 docs 通道落地自己那張票檔例外)。可行性只要一個小證據,不要完整解 ——
> 先做一遍的那一份,實作者還是得重做,而票面會被寫成那一份的形狀。

---

你是 **開題者**。開場只讀**角色卡**(上面規則包標題列的那個路徑,pack 解析過的,不要自己拼)+ **這張票**
(開題時就是下面的題目);模型記憶規則包已帶。其他要查的照角色卡「遇到就查」那一行。

## 這張票獨有的四件事(開題者這一格與 worker 不同,逐項寫明)
1. **題目**:`<使用者原話,逐字貼;不要轉述>`
2. **base sha**:`<sha>`(`git log --oneline -1 <sha>` 對過的那一個,寫進票的 `base_sha`)
3. **票庫路徑**:`<票庫路徑>`(`python3 scripts/ticket.py create …` 寫進去的地方)
4. **回報對象**:`<主線 / session 名>`

## 你要交的兩樣
1. **票 JSON** —— `python3 scripts/ticket.py create`(欄位契約見 `tickets/SCHEMA.md`,
   缺格它會逐條說)。票面最重要的一段是**測試計畫**:每條驗收寫「驗什麼行為 / 在哪一層驗
   (單元、API、瀏覽器)/ 怎麼驗(輸入、步驟、可觀察的輸出、期望值從哪裡來,**不從被測程式算**)
   / 功能標籤」。驗證者照這份計畫實作案例,**計畫寫不清就是開題者的錯**。
2. **摘要與建議順序** —— 寫進票的 `outline` 欄(`ticket.py create --outline '<≤300 字>'`)。
   **不要只回在對話裡**:對話裡那一份看板讀不到,而下一個接手的人手上只有票。

交完兩樣,**最後一步自己提交票檔**(D-025 ③;主線不再替你 commit):
`sh scripts/land.sh docs "tickets: #<n> 開票" tickets/<n>.json` ——
只准自己那張票檔(`AC_ROLE=opener` 時別的檔 `land.sh docs` 會擋,#91 C6)、只准這一個入口(鎖與前綴白名單都在它裡面),裸 commit 照舊禁止。

## 交票前自檢
- `tags` 只能用 `verify/TAGS.md`(與 `verify/TAGS.d/`)登記過的。
- 票面引用的指令**先 `--help` 一次再寫** —— 寫錯一個旗標,worker 會照著打到禁區埠。
- `decision_refs` 指到 `docs/DECISIONS.md` 真的存在的那幾條。
- `complexity`(#136):開題者每張票標 simple / complex;只有 complex 做完後覆核;沒標 = simple —— `ticket.py create --complexity simple|complex`;
  simple 票閘門綠就等 land,不派覆核。
- 交票前 `python3 scripts/ticket.py lint <n>`(#91 C3;唯讀):標籤登記過,它逐條說;
  沒過不轉 Ready。

## 設計題先派設計 session(D-019)
題目要決定形狀、要比較方案、會影響之後每一張票 —— 那是設計題:**先有 `docs/DESIGN-<題>.md`**
(設計 session 的產物),你照那份結論開票,不要自己邊開票邊設計。

## 票寫不出來的時候
症狀說不清、或使用者要的東西與現在的程式對不上 —— **說出來,不要硬開**。
一張猜出來的票會讓實作者、驗證者、閘門各走一趟,而三趟都是對的答案回答錯的問題。
