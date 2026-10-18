
- 交票前把 objective / acceptance / test_plan 三處對讀一遍:同一個行為在三處的範圍、數字、測試層級、要不要改舊案例必須說法一致,不一致先改票再轉 Ready —— 近期反駁約四成是票面自相矛盾 (T #509 #700 #683 #695) (2026-10-06, opener@opus)
- 需要驗證者的票,驗證者自己會建的案例檔與標籤片段不列進實作者的寫入範圍:兩份 patch 撞同一檔,整輪白跑;舊的「要列進去」規則已反轉 (T #709, A #97) (2026-10-06, opener@opus)
- out_of_scope 與 allowed_write_paths 都是建議範圍,硬擋只剩 config 的 protected_paths:真不准碰的寫進 protected_paths,out_of_scope 只寫「碰了要給理由」的;implementer / reviewer 卡「硬擋只有 out_of_scope、protected_paths」那句待整理角色改 (T #101, D-050 ①) (2026-10-18, worker@opus)
- 開題者卡首行「低於下限規則包會擋」已過時:#121 A8 拆掉規則包的 routing 下限,`pack` 不再以 routing / model_tiers 擋任何角色;模型合不合法只由 #116 選路器判,整理時把那半句換掉 (T #121) (2026-10-18, worker@opus)
- 實作票轉 Ready 前要有 plan 子指令實跑過的有效 plan_ran:命令存在不算跑過,非零 rc 照記;主線只動無關檔不必重跑,動到提示範圍或 test_plan 的測試才補跑 (T #106, D-050 ③) (2026-10-19, worker@opus)
