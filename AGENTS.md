# Agent Control — 給 Codex 與其他工具的入口

規則與 `CLAUDE.md` 相同;那份是權威,這份只是入口。先讀 `CLAUDE.md`、`docs/ROLES.md`、`docs/DISPATCH-TEMPLATE.md`。

你多半是 **worker**(實作者)或 **verifier**(驗證者 —— 只寫回歸案例並證明案例是對的,**不判 PASS/FAIL、不寫 VERDICT**)。兩者共同的硬牆:在副本裡工作、交 patch、禁一切 git 寫入、不連受保護的埠、不讀秘密目錄、每個結論標明「實測」或「推論」。
