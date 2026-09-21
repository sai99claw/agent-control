# 一票一個登記片段

新標籤寫進 `verify/TAGS.d/<票號>.md`,格式與 `verify/TAGS.md` 一樣:

```
- `nav-size` — 導覽列尺寸(#7)
```

`scripts/verify.py` 兩邊都認,所以片段還沒折進 `TAGS.md` 也不會紅;
`scripts/verify-case.py tags-merge` 把片段折進 `TAGS.md`(排序、去重、撞名出聲)。

**為什麼不直接改 `TAGS.md`**:所有票都往同一份檔的尾巴附加,等於每張票都要等前一張
落地才寫得下去(D-012 認過它是最常見的衝突點)。一票一個檔就不會撞,序列化的只剩
合併那一步。
